"""API Flask del liquidador. Por ahora solo Comercio (CCT 130/75)."""
import json
import os
from datetime import date
from decimal import Decimal, InvalidOperation
from io import BytesIO

from flask import Flask, abort, jsonify, render_template, request, send_file

from . import db as dbmod
from .calculo import Liquidacion, liquidar_comercio, primer_dia
from .config import Config
from .escalas import (CONVENIO_COMERCIO, basico_vigente, generar_plantilla, guardar_escala,
                      leer_plantilla)
from .recibo_pdf import DatosRecibo, generar_recibo


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


def _periodo(datos: dict) -> str:
    periodo = _requerido(datos, "periodo")
    try:
        primer_dia(periodo)
    except (ValueError, TypeError):
        raise ErrorDatos("'periodo' tiene que tener formato AAAA-MM")
    return periodo


def create_app(config: dict | None = None) -> Flask:
    app = Flask(__name__)
    app.config.from_object(Config)
    if config:
        app.config.update(config)
    app.teardown_appcontext(dbmod.close_db)

    with app.app_context():
        dbmod.init_schema(dbmod.get_db())

    @app.errorhandler(ErrorDatos)
    def _error_datos(exc):
        return jsonify(error=str(exc)), 400

    @app.get("/")
    def index():
        return render_template("index.html")

    # --- Escalas -----------------------------------------------------------
    @app.get("/escalas/plantilla")
    def descargar_plantilla():
        return send_file(BytesIO(generar_plantilla()), as_attachment=True,
                         download_name="plantilla_escala_comercio.xlsx",
                         mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")

    @app.post("/escalas/importar")
    def importar_escala():
        archivo = request.files.get("archivo")
        if archivo is None or not archivo.filename:
            raise ErrorDatos("Subí el Excel en el campo 'archivo'")
        if not archivo.filename.lower().endswith(".xlsx"):
            raise ErrorDatos("Solo se aceptan archivos .xlsx")
        res = leer_plantilla(archivo.read())
        if not res.ok:
            # No se carga nada si hay una sola fila mal: evita escalas a medias.
            return jsonify(error="La plantilla tiene errores, no se cargó nada", detalle=res.errores), 422
        n = guardar_escala(dbmod.get_db(), res.filas)
        return jsonify(importadas=n, convenio=CONVENIO_COMERCIO), 201

    @app.get("/escalas")
    def listar_escalas():
        rows = dbmod.get_db().execute(
            "SELECT categoria, monto, vigencia_desde FROM escalas WHERE convenio = ? "
            "ORDER BY vigencia_desde DESC, categoria", (CONVENIO_COMERCIO,)).fetchall()
        return jsonify([dict(r) for r in rows])

    # --- Empresas y empleados ---------------------------------------------
    @app.post("/empresas")
    def crear_empresa():
        d = request.get_json(force=True)
        cur = dbmod.get_db().execute(
            "INSERT INTO empresas (razon_social, cuit, domicilio) VALUES (?, ?, ?)",
            (_requerido(d, "razon_social"), _requerido(d, "cuit"), _requerido(d, "domicilio")))
        dbmod.get_db().commit()
        return jsonify(id=cur.lastrowid), 201

    @app.post("/empleados")
    def crear_empleado():
        d = request.get_json(force=True)
        conn = dbmod.get_db()
        empresa_id = _requerido(d, "empresa_id")
        if conn.execute("SELECT 1 FROM empresas WHERE id = ?", (empresa_id,)).fetchone() is None:
            raise ErrorDatos("La empresa no existe")
        cur = conn.execute(
            """INSERT INTO empleados (empresa_id, apellido, nombre, cuil, categoria,
                                      fecha_ingreso, afiliado_sindicato)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (empresa_id, _requerido(d, "apellido"), _requerido(d, "nombre"), _requerido(d, "cuil"),
             _requerido(d, "categoria"), _fecha(d, "fecha_ingreso").isoformat(),
             1 if d.get("afiliado_sindicato") else 0))
        conn.commit()
        return jsonify(id=cur.lastrowid), 201

    # --- Liquidaciones ----------------------------------------------------
    @app.post("/liquidaciones")
    def crear_liquidacion():
        d = request.get_json(force=True)
        conn = dbmod.get_db()
        emp = conn.execute("SELECT * FROM empleados WHERE id = ?", (_requerido(d, "empleado_id"),)).fetchone()
        if emp is None:
            raise ErrorDatos("El empleado no existe")
        periodo = _periodo(d)
        escala = basico_vigente(conn, emp["categoria"], primer_dia(periodo), emp["convenio"])
        if escala is None:
            raise ErrorDatos(f"No hay escala cargada para {emp['categoria']} vigente en {periodo}")
        tope = d.get("tope_base_imponible")
        try:
            tope = Decimal(str(tope)) if tope not in (None, "") else None
        except InvalidOperation:
            raise ErrorDatos("'tope_base_imponible' tiene que ser un número")
        try:
            liq = liquidar_comercio(
                periodo=periodo, categoria=emp["categoria"], basico=escala.monto,
                vigencia_escala=escala.vigencia_desde,
                fecha_ingreso=date.fromisoformat(emp["fecha_ingreso"]),
                afiliado_sindicato=bool(emp["afiliado_sindicato"]),
                inasistencias_injustificadas=int(d.get("inasistencias_injustificadas", 0)),
                tope_base_imponible=tope)
        except ValueError as exc:
            raise ErrorDatos(str(exc))
        resultado = {
            "liquidacion": liq.to_dict(),
            "ultimo_deposito": {
                "periodo": _requerido(d, "ultimo_deposito_periodo"),
                "fecha": _fecha(d, "ultimo_deposito_fecha").isoformat(),
                "banco": _requerido(d, "ultimo_deposito_banco"),
            },
        }
        cur = conn.execute(
            """INSERT INTO liquidaciones (empleado_id, periodo, fecha_pago, lugar_pago, resultado)
               VALUES (?, ?, ?, ?, ?)
               ON CONFLICT (empleado_id, periodo) DO UPDATE SET
                 fecha_pago = excluded.fecha_pago, lugar_pago = excluded.lugar_pago,
                 resultado = excluded.resultado
               RETURNING id""",
            (emp["id"], periodo, _fecha(d, "fecha_pago").isoformat(), _requerido(d, "lugar_pago"),
             json.dumps(resultado)))
        liq_id = cur.fetchone()[0]
        conn.commit()
        return jsonify(id=liq_id, **resultado["liquidacion"]), 201

    @app.get("/liquidaciones/<int:liq_id>/recibo.pdf")
    def recibo(liq_id: int):
        row = dbmod.get_db().execute(
            """SELECT l.*, e.apellido, e.nombre, e.cuil, e.fecha_ingreso, e.convenio,
                      x.razon_social, x.cuit, x.domicilio
               FROM liquidaciones l
               JOIN empleados e ON e.id = l.empleado_id
               JOIN empresas x ON x.id = e.empresa_id
               WHERE l.id = ?""", (liq_id,)).fetchone()
        if row is None:
            abort(404)
        buf = BytesIO()
        generar_recibo(buf, datos_recibo(row))
        buf.seek(0)
        return send_file(buf, mimetype="application/pdf", as_attachment=False,
                         download_name=f"recibo_{row['cuil']}_{row['periodo']}.pdf")

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
    )
