"""API Flask del liquidador.

Soporta varias empresas, cada una con sus empleados y cada empleado con su
convenio. Por ahora el único convenio con motor de cálculo es Comercio
(CCT 130/75); los demás se pueden dar de alta pero no liquidar.
"""
import json
import sqlite3
from datetime import date
from decimal import Decimal, InvalidOperation
from io import BytesIO

from flask import Flask, abort, jsonify, render_template, request, send_file

from . import db as dbmod
from .calculo import (Liquidacion, liquidar_comercio, liquidar_sac, primer_dia, semestre_de,
                      ultimo_dia)
from .config import Config
from .cuit import normalizar_cuit
from .datos_iniciales import cargar_escalas_iniciales, cargar_estructura
from .empleados import generar_plantilla_empleados, leer_empleados, validar_categoria
from .escalas import (CONVENIO_COMERCIO, basico_vigente, generar_plantilla, guardar_escala,
                      leer_plantilla)
from .recibo_pdf import DatosRecibo, generar_recibos

XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


class ErrorDatos(Exception):
    pass


def _requerido(datos: dict, campo: str):
    valor = datos.get(campo)
    if valor in (None, ""):
        raise ErrorDatos(f"Falta el campo '{campo}'")
    return valor


def _fecha(datos: dict, campo: str) -> date:
    try:
        return date.fromisoformat(_requerido(datos, campo))
    except (TypeError, ValueError):
        raise ErrorDatos(f"'{campo}' tiene que ser una fecha AAAA-MM-DD")


def _fecha_opcional(datos: dict, campo: str) -> date | None:
    return _fecha(datos, campo) if datos.get(campo) not in (None, "") else None


def _periodo(datos: dict) -> str:
    periodo = _requerido(datos, "periodo")
    try:
        primer_dia(periodo)
    except (ValueError, TypeError):
        raise ErrorDatos("'periodo' tiene que tener formato AAAA-MM")
    return periodo


def _decimal_opcional(datos: dict, campo: str) -> Decimal | None:
    valor = datos.get(campo)
    if valor in (None, ""):
        return None
    try:
        return Decimal(str(valor))
    except InvalidOperation:
        raise ErrorDatos(f"'{campo}' tiene que ser un número")


def _cuit(datos: dict, campo: str) -> str:
    try:
        return normalizar_cuit(_requerido(datos, campo))
    except ValueError as exc:
        raise ErrorDatos(str(exc))


def _archivo_xlsx():
    archivo = request.files.get("archivo")
    if archivo is None or not archivo.filename:
        raise ErrorDatos("Subí el Excel en el campo 'archivo'")
    if not archivo.filename.lower().endswith(".xlsx"):
        raise ErrorDatos("Solo se aceptan archivos .xlsx")
    return archivo


def _categorias(conn) -> dict:
    out = {}
    for r in conn.execute("SELECT convenio, nombre FROM categorias ORDER BY convenio, orden"):
        out.setdefault(r["convenio"], []).append(r["nombre"])
    for r in conn.execute("SELECT codigo FROM convenios"):
        out.setdefault(r["codigo"], [])
    return out


def _empresa(conn, empresa_id: int):
    row = conn.execute("SELECT * FROM empresas WHERE id = ?", (empresa_id,)).fetchone()
    if row is None:
        abort(404)
    return row


def _datos_pago(d: dict, empresa) -> dict:
    """Datos comunes a toda liquidación: pago y último depósito (art. 140 LCT)."""
    lugar = d.get("lugar_pago") or empresa["lugar_pago"]
    if not lugar:
        raise ErrorDatos("Falta el campo 'lugar_pago'")
    return {
        "fecha_pago": _fecha(d, "fecha_pago").isoformat(),
        "lugar_pago": lugar,
        "ultimo_deposito": {
            "periodo": _requerido(d, "ultimo_deposito_periodo"),
            "fecha": _fecha(d, "ultimo_deposito_fecha").isoformat(),
            "banco": _requerido(d, "ultimo_deposito_banco"),
        },
    }


def _guardar(conn, emp, liq: Liquidacion, pago: dict) -> int:
    resultado = {"liquidacion": liq.to_dict(), "ultimo_deposito": pago["ultimo_deposito"]}
    cur = conn.execute(
        """INSERT INTO liquidaciones (empleado_id, periodo, tipo, fecha_pago, lugar_pago, resultado)
           VALUES (?, ?, ?, ?, ?, ?)
           ON CONFLICT (empleado_id, periodo, tipo) DO UPDATE SET
             fecha_pago = excluded.fecha_pago, lugar_pago = excluded.lugar_pago,
             resultado = excluded.resultado
           RETURNING id""",
        (emp["id"], liq.periodo, liq.tipo, pago["fecha_pago"], pago["lugar_pago"], json.dumps(resultado)))
    return cur.fetchone()[0]


def _exigir_motor(conn, emp) -> None:
    conv = conn.execute("SELECT tiene_motor FROM convenios WHERE codigo = ?", (emp["convenio"],)).fetchone()
    if conv is None or not conv["tiene_motor"]:
        raise ErrorDatos(f"Todavía no hay motor de cálculo para el convenio {emp['convenio']}")


def liquidar_mensual(conn, emp, periodo: str, d: dict) -> Liquidacion:
    _exigir_motor(conn, emp)
    if emp["fecha_egreso"] and date.fromisoformat(emp["fecha_egreso"]) < primer_dia(periodo):
        raise ErrorDatos(f"El empleado egresó antes de {periodo}")
    escala = basico_vigente(conn, emp["categoria"], primer_dia(periodo), emp["convenio"])
    if escala is None:
        raise ErrorDatos(f"No hay escala cargada para {emp['categoria']} vigente en {periodo}")
    extraordinaria = _decimal_opcional(d, "asignacion_extraordinaria")
    if extraordinaria is None:
        # La de la escala es "única vez": solo si la vigencia es de este mismo mes.
        mismo_mes = escala.vigencia_desde.strftime("%Y-%m") == periodo
        extraordinaria = escala.asignacion_unica if mismo_mes else Decimal("0")
    try:
        return liquidar_comercio(
            periodo=periodo, categoria=emp["categoria"], basico=escala.monto,
            no_remunerativo=escala.no_remunerativo, vigencia_escala=escala.vigencia_desde,
            fecha_ingreso=date.fromisoformat(emp["fecha_ingreso"]),
            jornada_horas=emp["jornada_horas"], asignacion_extraordinaria=extraordinaria,
            inasistencias_injustificadas=int(d.get("inasistencias_injustificadas", 0)),
            tope_base_imponible=_decimal_opcional(d, "tope_base_imponible"))
    except ValueError as exc:
        raise ErrorDatos(str(exc))


def liquidar_aguinaldo(conn, emp, periodo: str, d: dict) -> Liquidacion:
    _exigir_motor(conn, emp)
    egreso_en_mes = bool(emp["fecha_egreso"]) and emp["fecha_egreso"][:7] == periodo
    if int(periodo[5:]) not in (6, 12) and not egreso_en_mes:
        raise ErrorDatos("El SAC se liquida en junio, en diciembre o en el mes del egreso")
    rows = conn.execute(
        "SELECT resultado FROM liquidaciones WHERE empleado_id = ? AND tipo = 'mensual'",
        (emp["id"],)).fetchall()
    historial = [Liquidacion.from_dict(json.loads(r["resultado"])["liquidacion"]) for r in rows]
    try:
        return liquidar_sac(
            periodo=periodo, categoria=emp["categoria"],
            fecha_ingreso=date.fromisoformat(emp["fecha_ingreso"]),
            fecha_egreso=date.fromisoformat(emp["fecha_egreso"]) if emp["fecha_egreso"] else None,
            jornada_horas=emp["jornada_horas"], historial=historial,
            tope_base_imponible=_decimal_opcional(d, "tope_base_imponible"))
    except ValueError as exc:
        raise ErrorDatos(str(exc))


def _empleados_entre(conn, empresa_id: int, desde: date, hasta: date):
    """Empleados que trabajaron al menos un día entre `desde` y `hasta`."""
    return conn.execute(
        """SELECT * FROM empleados WHERE empresa_id = ? AND fecha_ingreso <= ?
             AND (fecha_egreso IS NULL OR fecha_egreso >= ?)
           ORDER BY apellido, nombre""",
        (empresa_id, hasta.isoformat(), desde.isoformat())).fetchall()


def create_app(config: dict | None = None) -> Flask:
    app = Flask(__name__)
    app.config.from_object(Config)
    if config:
        app.config.update(config)
    app.teardown_appcontext(dbmod.close_db)

    with app.app_context():
        conn = dbmod.get_db()
        dbmod.init_schema(conn)
        cargar_estructura(conn)
        if app.config.get("CARGAR_ESCALAS_INICIALES", True):
            cargar_escalas_iniciales(conn)

    @app.errorhandler(ErrorDatos)
    def _error_datos(exc):
        return jsonify(error=str(exc)), 400

    @app.get("/")
    def index():
        return render_template("index.html")

    # --- Convenios y categorías -------------------------------------------
    @app.get("/convenios")
    def listar_convenios():
        conn = dbmod.get_db()
        cats = _categorias(conn)
        return jsonify([{**dict(r), "tiene_motor": bool(r["tiene_motor"]), "categorias": cats[r["codigo"]]}
                        for r in conn.execute("SELECT * FROM convenios ORDER BY codigo")])

    @app.post("/convenios")
    def crear_convenio():
        """Alta de otro convenio con sus categorías (sin motor de cálculo todavía)."""
        d = request.get_json(force=True)
        conn = dbmod.get_db()
        codigo = _requerido(d, "codigo")
        categorias = d.get("categorias") or []
        if not isinstance(categorias, list) or not all(isinstance(c, str) and c.strip() for c in categorias):
            raise ErrorDatos("'categorias' tiene que ser una lista de nombres")
        if conn.execute("SELECT 1 FROM convenios WHERE codigo = ?", (codigo,)).fetchone():
            raise ErrorDatos(f"El convenio {codigo} ya existe")
        conn.execute("INSERT INTO convenios (codigo, nombre, tiene_motor) VALUES (?, ?, 0)",
                     (codigo, _requerido(d, "nombre")))
        for orden, cat in enumerate(categorias):
            conn.execute("INSERT OR IGNORE INTO categorias (convenio, nombre, orden) VALUES (?, ?, ?)",
                         (codigo, cat.strip(), orden))
        conn.commit()
        return jsonify(codigo=codigo, categorias=len(categorias)), 201

    # --- Escalas -----------------------------------------------------------
    @app.get("/escalas/plantilla")
    def descargar_plantilla():
        return send_file(BytesIO(generar_plantilla()), as_attachment=True,
                         download_name="plantilla_escala_comercio.xlsx", mimetype=XLSX)

    @app.post("/escalas/importar")
    def importar_escala():
        archivo = _archivo_xlsx()
        res = leer_plantilla(archivo.read())
        if not res.ok:
            # No se carga nada si hay una sola fila mal: evita escalas a medias.
            return jsonify(error="La plantilla tiene errores, no se cargó nada", detalle=res.errores), 422
        n = guardar_escala(dbmod.get_db(), res.filas, fuente=f"Importada de {archivo.filename}")
        return jsonify(importadas=n, convenio=CONVENIO_COMERCIO), 201

    @app.get("/escalas")
    def listar_escalas():
        rows = dbmod.get_db().execute(
            """SELECT categoria, monto, vigencia_desde, no_remunerativo, asignacion_unica, fuente, verificada
               FROM escalas WHERE convenio = ?
               ORDER BY vigencia_desde DESC, categoria""",
            (request.args.get("convenio", CONVENIO_COMERCIO),)).fetchall()
        return jsonify([{**dict(r), "verificada": bool(r["verificada"])} for r in rows])

    # --- Empresas ----------------------------------------------------------
    @app.post("/empresas")
    def crear_empresa():
        d = request.get_json(force=True)
        conn = dbmod.get_db()
        cuit = _cuit(d, "cuit")
        if conn.execute("SELECT 1 FROM empresas WHERE cuit = ?", (cuit,)).fetchone():
            raise ErrorDatos(f"Ya existe una empresa con CUIT {cuit}")
        cur = conn.execute(
            "INSERT INTO empresas (razon_social, cuit, domicilio, lugar_pago) VALUES (?, ?, ?, ?)",
            (_requerido(d, "razon_social"), cuit, _requerido(d, "domicilio"), d.get("lugar_pago")))
        conn.commit()
        return jsonify(id=cur.lastrowid, cuit=cuit), 201

    @app.get("/empresas")
    def listar_empresas():
        rows = dbmod.get_db().execute(
            """SELECT x.*, (SELECT COUNT(*) FROM empleados e WHERE e.empresa_id = x.id
                             AND e.fecha_egreso IS NULL) AS empleados_activos
               FROM empresas x ORDER BY razon_social""").fetchall()
        return jsonify([dict(r) for r in rows])

    @app.get("/empresas/<int:empresa_id>/empleados")
    def listar_empleados(empresa_id: int):
        conn = dbmod.get_db()
        _empresa(conn, empresa_id)
        rows = conn.execute("SELECT * FROM empleados WHERE empresa_id = ? ORDER BY apellido, nombre",
                            (empresa_id,)).fetchall()
        return jsonify([dict(r) for r in rows])

    # --- Empleados ---------------------------------------------------------
    def _guardar_empleado(conn, empresa_id: int, f: dict) -> int:
        """Alta o actualización por CUIL dentro de la empresa."""
        cur = conn.execute(
            """INSERT INTO empleados (empresa_id, legajo, apellido, nombre, cuil, convenio, categoria,
                                      fecha_ingreso, jornada_horas, fecha_egreso)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT (empresa_id, cuil) DO UPDATE SET
                 legajo = excluded.legajo, apellido = excluded.apellido, nombre = excluded.nombre,
                 convenio = excluded.convenio, categoria = excluded.categoria,
                 fecha_ingreso = excluded.fecha_ingreso, jornada_horas = excluded.jornada_horas,
                 fecha_egreso = excluded.fecha_egreso
               RETURNING id""",
            (empresa_id, f["legajo"], f["apellido"], f["nombre"], f["cuil"], f["convenio"], f["categoria"],
             f["fecha_ingreso"], f["jornada_horas"], f["fecha_egreso"]))
        return cur.fetchone()[0]

    @app.post("/empleados")
    def crear_empleado():
        d = request.get_json(force=True)
        conn = dbmod.get_db()
        empresa_id = _requerido(d, "empresa_id")
        if conn.execute("SELECT 1 FROM empresas WHERE id = ?", (empresa_id,)).fetchone() is None:
            raise ErrorDatos("La empresa no existe")
        try:
            jornada = int(d.get("jornada_horas", 8))
        except (TypeError, ValueError):
            raise ErrorDatos("'jornada_horas' tiene que ser un entero")
        if not 1 <= jornada <= 8:
            raise ErrorDatos("'jornada_horas' tiene que estar entre 1 y 8")
        convenio = d.get("convenio") or CONVENIO_COMERCIO
        try:
            categoria = validar_categoria(convenio, _requerido(d, "categoria"), _categorias(conn))
        except ValueError as exc:
            raise ErrorDatos(str(exc))
        egreso = _fecha_opcional(d, "fecha_egreso")
        try:
            emp_id = _guardar_empleado(conn, empresa_id, {
                "legajo": d.get("legajo"), "apellido": _requerido(d, "apellido"),
                "nombre": _requerido(d, "nombre"), "cuil": _cuit(d, "cuil"), "convenio": convenio,
                "categoria": categoria, "fecha_ingreso": _fecha(d, "fecha_ingreso").isoformat(),
                "jornada_horas": jornada, "fecha_egreso": egreso.isoformat() if egreso else None})
        except sqlite3.IntegrityError as exc:
            raise ErrorDatos(f"No se pudo guardar el empleado: {exc}")
        conn.commit()
        return jsonify(id=emp_id), 201

    @app.get("/empleados/plantilla")
    def plantilla_empleados():
        contenido = generar_plantilla_empleados(_categorias(dbmod.get_db()))
        return send_file(BytesIO(contenido), as_attachment=True,
                         download_name="plantilla_empleados.xlsx", mimetype=XLSX)

    @app.post("/empresas/<int:empresa_id>/empleados/importar")
    def importar_empleados(empresa_id: int):
        conn = dbmod.get_db()
        _empresa(conn, empresa_id)
        filas, errores = leer_empleados(_archivo_xlsx().read(), _categorias(conn))
        if errores:
            return jsonify(error="La planilla tiene errores, no se cargó nada", detalle=errores), 422
        try:
            for f in filas:
                _guardar_empleado(conn, empresa_id, {
                    **f.__dict__, "fecha_ingreso": f.fecha_ingreso.isoformat(),
                    "fecha_egreso": f.fecha_egreso.isoformat() if f.fecha_egreso else None})
        except sqlite3.IntegrityError as exc:
            conn.rollback()
            raise ErrorDatos(f"No se cargó nada: {exc}")
        conn.commit()
        return jsonify(importados=len(filas)), 201

    # --- Liquidaciones ----------------------------------------------------
    def _empleado(conn, d: dict):
        emp = conn.execute("SELECT * FROM empleados WHERE id = ?", (_requerido(d, "empleado_id"),)).fetchone()
        if emp is None:
            raise ErrorDatos("El empleado no existe")
        return emp

    def _liquidar_uno(funcion):
        d = request.get_json(force=True)
        conn = dbmod.get_db()
        emp = _empleado(conn, d)
        pago = _datos_pago(d, _empresa(conn, emp["empresa_id"]))
        liq = funcion(conn, emp, _periodo(d), d)
        liq_id = _guardar(conn, emp, liq, pago)
        conn.commit()
        return jsonify(id=liq_id, **liq.to_dict()), 201

    @app.post("/liquidaciones")
    def crear_liquidacion():
        return _liquidar_uno(liquidar_mensual)

    @app.post("/liquidaciones/sac")
    def crear_sac():
        return _liquidar_uno(liquidar_aguinaldo)

    def _liquidar_empresa(empresa_id: int, funcion, desde_hasta):
        d = request.get_json(force=True)
        conn = dbmod.get_db()
        empresa = _empresa(conn, empresa_id)
        periodo = _periodo(d)
        pago = _datos_pago(d, empresa)
        hechas, errores = [], []
        for emp in _empleados_entre(conn, empresa_id, *desde_hasta(periodo)):
            try:
                liq = funcion(conn, emp, periodo, d)
            except ErrorDatos as exc:
                errores.append({"empleado_id": emp["id"], "legajo": emp["legajo"], "error": str(exc)})
                continue
            hechas.append({"id": _guardar(conn, emp, liq, pago), "empleado_id": emp["id"],
                           "legajo": emp["legajo"], "neto": str(liq.neto)})
        conn.commit()
        return jsonify(periodo=periodo, liquidaciones=hechas, errores=errores), (201 if hechas else 400)

    @app.post("/empresas/<int:empresa_id>/liquidaciones")
    def liquidar_empresa(empresa_id: int):
        """Liquida el mes a todos los empleados activos de la empresa."""
        return _liquidar_empresa(empresa_id, liquidar_mensual, lambda p: (primer_dia(p), ultimo_dia(p)))

    @app.post("/empresas/<int:empresa_id>/sac")
    def sac_empresa(empresa_id: int):
        """Liquida el SAC a todos los que trabajaron en el semestre."""
        return _liquidar_empresa(empresa_id, liquidar_aguinaldo, semestre_de)

    SELECT_RECIBO = """SELECT l.*, e.apellido, e.nombre, e.cuil, e.fecha_ingreso, e.convenio, e.legajo,
                              x.razon_social, x.cuit, x.domicilio
                       FROM liquidaciones l
                       JOIN empleados e ON e.id = l.empleado_id
                       JOIN empresas x ON x.id = e.empresa_id"""

    def _pdf(rows, nombre: str):
        buf = BytesIO()
        generar_recibos(buf, [datos_recibo(r) for r in rows])
        buf.seek(0)
        return send_file(buf, mimetype="application/pdf", as_attachment=False, download_name=nombre)

    @app.get("/liquidaciones/<int:liq_id>/recibo.pdf")
    def recibo(liq_id: int):
        row = dbmod.get_db().execute(SELECT_RECIBO + " WHERE l.id = ?", (liq_id,)).fetchone()
        if row is None:
            abort(404)
        return _pdf([row], f"recibo_{row['cuil']}_{row['periodo']}_{row['tipo']}.pdf")

    @app.get("/empresas/<int:empresa_id>/recibos/<periodo>.pdf")
    def recibos_empresa(empresa_id: int, periodo: str):
        """Todos los recibos de la empresa para el período, en un solo PDF (?tipo=sac para el SAC)."""
        tipo = request.args.get("tipo", "mensual")
        rows = dbmod.get_db().execute(
            SELECT_RECIBO + " WHERE e.empresa_id = ? AND l.periodo = ? AND l.tipo = ?"
                            " ORDER BY e.apellido, e.nombre",
            (empresa_id, periodo, tipo)).fetchall()
        if not rows:
            abort(404)
        return _pdf(rows, f"recibos_{rows[0]['cuit']}_{periodo}_{tipo}.pdf")

    return app


def datos_recibo(row) -> DatosRecibo:
    resultado = json.loads(row["resultado"])
    dep = resultado["ultimo_deposito"]
    return DatosRecibo(
        empresa_razon_social=row["razon_social"], empresa_cuit=row["cuit"],
        empresa_domicilio=row["domicilio"],
        empleado_apellido=row["apellido"], empleado_nombre=row["nombre"],
        empleado_cuil=row["cuil"], empleado_fecha_ingreso=date.fromisoformat(row["fecha_ingreso"]),
        convenio=row["convenio"], liquidacion=Liquidacion.from_dict(resultado["liquidacion"]),
        fecha_pago=date.fromisoformat(row["fecha_pago"]), lugar_pago=row["lugar_pago"],
        ultimo_deposito_periodo=dep["periodo"],
        ultimo_deposito_fecha=date.fromisoformat(dep["fecha"]),
        ultimo_deposito_banco=dep["banco"],
        empleado_legajo=row["legajo"],
    )
