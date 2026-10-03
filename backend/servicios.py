"""Operaciones del liquidador, compartidas por la API JSON y las pantallas web.

Cada función recibe la conexión y un dict con los datos (JSON o formulario) y
lanza `ErrorDatos` con un mensaje para el usuario si algo no cierra.
"""
import json
import sqlite3
from datetime import date
from decimal import Decimal, InvalidOperation
from io import BytesIO

from .calculo import (CAUSAS_EGRESO, Liquidacion, dias_del_mes, liquidar_comercio, liquidar_final,
                      liquidar_sac, primer_dia, semestre_de, ultimo_dia)
from .cuit import normalizar_cuit
from .empleados import leer_empleados, validar_categoria
from .escalas import CONVENIO_COMERCIO, basico_vigente, guardar_escala, leer_plantilla
from .recibo_pdf import DatosRecibo, generar_recibos


class ErrorDatos(Exception):
    def __init__(self, mensaje: str, detalle: list | None = None):
        super().__init__(mensaje)
        self.detalle = detalle or []


# --- Lectura de datos de entrada -------------------------------------------
def requerido(datos: dict, campo: str):
    valor = datos.get(campo)
    if valor in (None, ""):
        raise ErrorDatos(f"Falta el campo '{campo}'")
    return valor


def fecha(datos: dict, campo: str) -> date:
    try:
        return date.fromisoformat(requerido(datos, campo))
    except (TypeError, ValueError):
        raise ErrorDatos(f"'{campo}' tiene que ser una fecha AAAA-MM-DD")


def fecha_opcional(datos: dict, campo: str) -> date | None:
    return fecha(datos, campo) if datos.get(campo) not in (None, "") else None


def periodo(datos: dict) -> str:
    valor = requerido(datos, "periodo")
    try:
        primer_dia(valor)
    except (ValueError, TypeError):
        raise ErrorDatos("'periodo' tiene que tener formato AAAA-MM")
    return valor


def decimal_opcional(datos: dict, campo: str) -> Decimal | None:
    valor = datos.get(campo)
    if valor in (None, ""):
        return None
    try:
        return Decimal(str(valor).replace(",", "."))
    except InvalidOperation:
        raise ErrorDatos(f"'{campo}' tiene que ser un número")


def entero(datos: dict, campo: str, default: int) -> int:
    valor = datos.get(campo)
    if valor in (None, ""):
        return default
    try:
        return int(valor)
    except (TypeError, ValueError):
        raise ErrorDatos(f"'{campo}' tiene que ser un entero")


def cuit(datos: dict, campo: str) -> str:
    try:
        return normalizar_cuit(requerido(datos, campo))
    except ValueError as exc:
        raise ErrorDatos(str(exc))


# --- Consultas ---------------------------------------------------------------
def categorias(conn) -> dict:
    out = {r["codigo"]: [] for r in conn.execute("SELECT codigo FROM convenios ORDER BY codigo")}
    for r in conn.execute("SELECT convenio, nombre FROM categorias ORDER BY convenio, orden"):
        out.setdefault(r["convenio"], []).append(r["nombre"])
    return out


def convenios(conn) -> list:
    cats = categorias(conn)
    return [{**dict(r), "tiene_motor": bool(r["tiene_motor"]), "categorias": cats[r["codigo"]]}
            for r in conn.execute("SELECT * FROM convenios ORDER BY codigo")]


def empresas(conn) -> list:
    return [dict(r) for r in conn.execute(
        """SELECT x.*, (SELECT COUNT(*) FROM empleados e WHERE e.empresa_id = x.id
                         AND e.fecha_egreso IS NULL) AS empleados_activos
           FROM empresas x ORDER BY razon_social""")]


def empresa(conn, empresa_id: int):
    return conn.execute("SELECT * FROM empresas WHERE id = ?", (empresa_id,)).fetchone()


def empleados(conn, empresa_id: int) -> list:
    return [dict(r) for r in conn.execute(
        "SELECT * FROM empleados WHERE empresa_id = ? ORDER BY fecha_egreso IS NOT NULL, apellido, nombre",
        (empresa_id,))]


def escalas(conn, convenio: str = CONVENIO_COMERCIO) -> list:
    return [{**dict(r), "verificada": bool(r["verificada"])} for r in conn.execute(
        """SELECT categoria, monto, vigencia_desde, no_remunerativo, asignacion_unica, fuente, verificada
           FROM escalas WHERE convenio = ? ORDER BY vigencia_desde DESC, categoria""", (convenio,))]


def liquidaciones_por_periodo(conn, empresa_id: int) -> list:
    """Resumen de lo liquidado en la empresa: un renglón por período y tipo."""
    filas = conn.execute(
        """SELECT l.periodo, l.tipo, l.resultado FROM liquidaciones l
           JOIN empleados e ON e.id = l.empleado_id WHERE e.empresa_id = ?""", (empresa_id,)).fetchall()
    resumen = {}
    for f in filas:
        r = resumen.setdefault((f["periodo"], f["tipo"]), {"periodo": f["periodo"], "tipo": f["tipo"],
                                                           "recibos": 0, "neto": Decimal("0")})
        r["recibos"] += 1
        r["neto"] += Decimal(json.loads(f["resultado"])["liquidacion"]["neto"])
    return sorted(resumen.values(), key=lambda r: (r["periodo"], r["tipo"]), reverse=True)


def liquidaciones_de(conn, empresa_id: int, periodo_: str, tipo: str) -> list:
    return [{"id": r["id"], "legajo": r["legajo"], "empleado": f"{r['apellido']}, {r['nombre']}",
             "categoria": r["categoria"], **{k: json.loads(r["resultado"])["liquidacion"][k]
                                              for k in ("total_remunerativo", "total_no_remunerativo",
                                                        "total_descuentos", "neto")}}
            for r in conn.execute(
                """SELECT l.id, l.resultado, e.legajo, e.apellido, e.nombre, e.categoria
                   FROM liquidaciones l JOIN empleados e ON e.id = l.empleado_id
                   WHERE e.empresa_id = ? AND l.periodo = ? AND l.tipo = ?
                   ORDER BY e.apellido, e.nombre""", (empresa_id, periodo_, tipo))]


def ultimo_pago(conn, empresa_id: int) -> dict:
    """Datos de pago de la última liquidación de la empresa, para precargar el formulario."""
    r = conn.execute(
        """SELECT l.fecha_pago, l.lugar_pago, l.resultado FROM liquidaciones l
           JOIN empleados e ON e.id = l.empleado_id WHERE e.empresa_id = ?
           ORDER BY l.creada DESC, l.id DESC LIMIT 1""", (empresa_id,)).fetchone()
    if r is None:
        return {}
    dep = json.loads(r["resultado"])["ultimo_deposito"]
    return {"lugar_pago": r["lugar_pago"], "ultimo_deposito_periodo": dep["periodo"],
            "ultimo_deposito_fecha": dep["fecha"], "ultimo_deposito_banco": dep["banco"]}


# --- Altas e importaciones ---------------------------------------------------
def crear_empresa(conn, d: dict) -> int:
    numero = cuit(d, "cuit")
    if conn.execute("SELECT 1 FROM empresas WHERE cuit = ?", (numero,)).fetchone():
        raise ErrorDatos(f"Ya existe una empresa con CUIT {numero}")
    cur = conn.execute(
        "INSERT INTO empresas (razon_social, cuit, domicilio, lugar_pago) VALUES (?, ?, ?, ?)",
        (requerido(d, "razon_social").strip(), numero, requerido(d, "domicilio").strip(),
         (d.get("lugar_pago") or "").strip() or None))
    conn.commit()
    return cur.lastrowid


def crear_convenio(conn, d: dict) -> str:
    codigo = requerido(d, "codigo").strip()
    cats = d.get("categorias") or []
    if not isinstance(cats, list) or not all(isinstance(c, str) and c.strip() for c in cats):
        raise ErrorDatos("'categorias' tiene que ser una lista de nombres")
    if conn.execute("SELECT 1 FROM convenios WHERE codigo = ?", (codigo,)).fetchone():
        raise ErrorDatos(f"El convenio {codigo} ya existe")
    conn.execute("INSERT INTO convenios (codigo, nombre, tiene_motor) VALUES (?, ?, 0)",
                 (codigo, requerido(d, "nombre")))
    for orden, cat in enumerate(cats):
        conn.execute("INSERT OR IGNORE INTO categorias (convenio, nombre, orden) VALUES (?, ?, ?)",
                     (codigo, cat.strip(), orden))
    conn.commit()
    return codigo


def _upsert_empleado(conn, empresa_id: int, f: dict) -> int:
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


def guardar_empleado(conn, empresa_id: int, d: dict) -> int:
    if empresa(conn, empresa_id) is None:
        raise ErrorDatos("La empresa no existe")
    jornada = entero(d, "jornada_horas", 8)
    if not 1 <= jornada <= 8:
        raise ErrorDatos("'jornada_horas' tiene que estar entre 1 y 8")
    convenio = d.get("convenio") or CONVENIO_COMERCIO
    try:
        categoria = validar_categoria(convenio, requerido(d, "categoria"), categorias(conn))
    except ValueError as exc:
        raise ErrorDatos(str(exc))
    ingreso, egreso = fecha(d, "fecha_ingreso"), fecha_opcional(d, "fecha_egreso")
    if egreso and egreso < ingreso:
        raise ErrorDatos("La fecha de egreso es anterior a la de ingreso")
    try:
        emp_id = _upsert_empleado(conn, empresa_id, {
            "legajo": (d.get("legajo") or "").strip() or None,
            "apellido": requerido(d, "apellido").strip(), "nombre": requerido(d, "nombre").strip(),
            "cuil": cuit(d, "cuil"), "convenio": convenio, "categoria": categoria,
            "fecha_ingreso": ingreso.isoformat(), "jornada_horas": jornada,
            "fecha_egreso": egreso.isoformat() if egreso else None})
    except sqlite3.IntegrityError as exc:
        raise ErrorDatos(f"No se pudo guardar el empleado: {exc}")
    conn.commit()
    return emp_id


def importar_empleados(conn, empresa_id: int, contenido: bytes) -> int:
    if empresa(conn, empresa_id) is None:
        raise ErrorDatos("La empresa no existe")
    filas, errores = leer_empleados(contenido, categorias(conn))
    if errores:
        raise ErrorDatos("La planilla tiene errores, no se cargó nada", errores)
    try:
        for f in filas:
            _upsert_empleado(conn, empresa_id, {
                **f.__dict__, "fecha_ingreso": f.fecha_ingreso.isoformat(),
                "fecha_egreso": f.fecha_egreso.isoformat() if f.fecha_egreso else None})
    except sqlite3.IntegrityError as exc:
        conn.rollback()
        raise ErrorDatos(f"No se cargó nada: {exc}")
    conn.commit()
    return len(filas)


def importar_escala(conn, contenido: bytes, nombre_archivo: str) -> int:
    res = leer_plantilla(contenido)
    if not res.ok:
        # No se carga nada si hay una sola fila mal: evita escalas a medias.
        raise ErrorDatos("La plantilla tiene errores, no se cargó nada", res.errores)
    return guardar_escala(conn, res.filas, fuente=f"Importada de {nombre_archivo}")


# --- Liquidaciones -------------------------------------------------------------
def _exigir_motor(conn, emp) -> None:
    conv = conn.execute("SELECT tiene_motor FROM convenios WHERE codigo = ?", (emp["convenio"],)).fetchone()
    if conv is None or not conv["tiene_motor"]:
        raise ErrorDatos(f"Todavía no hay motor de cálculo para el convenio {emp['convenio']}")


def liquidar_mensual(conn, emp, periodo_: str, d: dict) -> Liquidacion:
    _exigir_motor(conn, emp)
    if emp["fecha_egreso"] and date.fromisoformat(emp["fecha_egreso"]) < primer_dia(periodo_):
        raise ErrorDatos(f"El empleado egresó antes de {periodo_}")
    if _final_entre(conn, emp["id"], periodo_, periodo_):
        raise ErrorDatos(f"Ya tiene la liquidación final de {periodo_}")
    escala, extraordinaria = _escala_del_mes(conn, emp, periodo_, d)
    ingreso = date.fromisoformat(emp["fecha_ingreso"])
    egreso = date.fromisoformat(emp["fecha_egreso"]) if emp["fecha_egreso"] else None
    dias = dias_del_mes(periodo_, ingreso, egreso)
    if dias == 0:
        raise ErrorDatos(f"No trabajó en {periodo_}")
    try:
        return liquidar_comercio(
            periodo=periodo_, categoria=emp["categoria"], basico=escala.monto,
            no_remunerativo=escala.no_remunerativo, vigencia_escala=escala.vigencia_desde,
            fecha_ingreso=ingreso, jornada_horas=emp["jornada_horas"],
            asignacion_extraordinaria=extraordinaria, dias=dias,
            inasistencias_injustificadas=entero(d, "inasistencias_injustificadas", 0),
            tope_base_imponible=decimal_opcional(d, "tope_base_imponible"))
    except ValueError as exc:
        raise ErrorDatos(str(exc))


def _escala_del_mes(conn, emp, periodo_: str, d: dict):
    escala = basico_vigente(conn, emp["categoria"], primer_dia(periodo_), emp["convenio"])
    if escala is None:
        raise ErrorDatos(f"No hay escala cargada para {emp['categoria']} vigente en {periodo_}")
    extraordinaria = decimal_opcional(d, "asignacion_extraordinaria")
    if extraordinaria is None:
        # La de la escala es "única vez": solo si la vigencia es de este mismo mes.
        mismo_mes = escala.vigencia_desde.strftime("%Y-%m") == periodo_
        extraordinaria = escala.asignacion_unica if mismo_mes else Decimal("0")
    return escala, extraordinaria


def _final_entre(conn, empleado_id: int, desde: str, hasta: str):
    """Liquidación final del empleado con período entre `desde` y `hasta` (AAAA-MM), si hay."""
    return conn.execute(
        "SELECT periodo FROM liquidaciones WHERE empleado_id = ? AND tipo = 'final' AND periodo BETWEEN ? AND ?",
        (empleado_id, desde, hasta)).fetchone()


def _historial(conn, empleado_id: int) -> list:
    rows = conn.execute(
        "SELECT resultado FROM liquidaciones WHERE empleado_id = ? AND tipo = 'mensual'",
        (empleado_id,)).fetchall()
    return [Liquidacion.from_dict(json.loads(r["resultado"])["liquidacion"]) for r in rows]


def liquidar_aguinaldo(conn, emp, periodo_: str, d: dict) -> Liquidacion:
    _exigir_motor(conn, emp)
    egreso_en_mes = bool(emp["fecha_egreso"]) and emp["fecha_egreso"][:7] == periodo_
    if int(periodo_[5:]) not in (6, 12) and not egreso_en_mes:
        raise ErrorDatos("El SAC se liquida en junio, en diciembre o en el mes del egreso")
    inicio, fin = semestre_de(periodo_)
    final = _final_entre(conn, emp["id"], inicio.strftime("%Y-%m"), fin.strftime("%Y-%m"))
    if final:
        raise ErrorDatos(f"El SAC proporcional ya se pagó en la liquidación final de {final['periodo']}")
    historial = _historial(conn, emp["id"])
    try:
        return liquidar_sac(
            periodo=periodo_, categoria=emp["categoria"],
            fecha_ingreso=date.fromisoformat(emp["fecha_ingreso"]),
            fecha_egreso=date.fromisoformat(emp["fecha_egreso"]) if emp["fecha_egreso"] else None,
            jornada_horas=emp["jornada_horas"], historial=historial,
            tope_base_imponible=decimal_opcional(d, "tope_base_imponible"))
    except ValueError as exc:
        raise ErrorDatos(str(exc))


FUNCIONES = {"mensual": liquidar_mensual, "sac": liquidar_aguinaldo}


def liquidar_egreso(conn, empleado_id: int, d: dict) -> tuple[int, Liquidacion]:
    """Liquidación final: guarda la fecha de egreso y reemplaza el sueldo y el SAC de ese mes."""
    emp = conn.execute("SELECT * FROM empleados WHERE id = ?", (empleado_id,)).fetchone()
    if emp is None:
        raise ErrorDatos("El empleado no existe")
    _exigir_motor(conn, emp)
    causa = requerido(d, "causa")
    if causa not in CAUSAS_EGRESO:
        raise ErrorDatos(f"Causa de egreso desconocida: {causa}")
    egreso = fecha(d, "fecha_egreso")
    ingreso = date.fromisoformat(emp["fecha_ingreso"])
    if egreso < ingreso:
        raise ErrorDatos("La fecha de egreso es anterior a la de ingreso")
    periodo_ = egreso.strftime("%Y-%m")
    pago = datos_pago(d, empresa(conn, emp["empresa_id"]))
    escala, extraordinaria = _escala_del_mes(conn, emp, periodo_, d)
    try:
        liq = liquidar_final(
            categoria=emp["categoria"], basico=escala.monto, no_remunerativo=escala.no_remunerativo,
            vigencia_escala=escala.vigencia_desde, fecha_ingreso=ingreso, fecha_egreso=egreso,
            causa=causa, historial=_historial(conn, emp["id"]), jornada_horas=emp["jornada_horas"],
            asignacion_extraordinaria=extraordinaria,
            inasistencias_injustificadas=entero(d, "inasistencias_injustificadas", 0),
            preaviso_otorgado=str(d.get("preaviso_otorgado", "")).lower() in ("1", "true", "si", "sí", "on"),
            vacaciones_gozadas=decimal_opcional(d, "vacaciones_gozadas") or Decimal("0"),
            tope_indemnizatorio=decimal_opcional(d, "tope_indemnizatorio"),
            tope_base_imponible=decimal_opcional(d, "tope_base_imponible"))
    except ValueError as exc:
        raise ErrorDatos(str(exc))
    conn.execute("UPDATE empleados SET fecha_egreso = ? WHERE id = ?", (egreso.isoformat(), emp["id"]))
    # La final reemplaza al sueldo y al SAC del mes del egreso, y a una final anterior mal cargada.
    conn.execute("""DELETE FROM liquidaciones WHERE empleado_id = ?
                      AND ((periodo = ? AND tipo IN ('mensual', 'sac')) OR (tipo = 'final' AND periodo <> ?))""",
                 (emp["id"], periodo_, periodo_))
    liq_id = _guardar_liquidacion(conn, emp, liq, pago)
    conn.commit()
    return liq_id, liq


def liquidacion(conn, liq_id: int):
    """Una liquidación con los datos del empleado, para mostrarla en pantalla."""
    r = conn.execute(
        """SELECT l.*, e.apellido, e.nombre, e.empresa_id, e.legajo FROM liquidaciones l
           JOIN empleados e ON e.id = l.empleado_id WHERE l.id = ?""", (liq_id,)).fetchone()
    if r is None:
        return None
    return {**dict(r), "liq": Liquidacion.from_dict(json.loads(r["resultado"])["liquidacion"])}


def datos_pago(d: dict, emp_empresa) -> dict:
    """Datos comunes a toda liquidación: pago y último depósito (art. 140 LCT)."""
    lugar = (d.get("lugar_pago") or "").strip() or emp_empresa["lugar_pago"]
    if not lugar:
        raise ErrorDatos("Falta el campo 'lugar_pago'")
    return {
        "fecha_pago": fecha(d, "fecha_pago").isoformat(),
        "lugar_pago": lugar,
        "ultimo_deposito": {
            "periodo": requerido(d, "ultimo_deposito_periodo"),
            "fecha": fecha(d, "ultimo_deposito_fecha").isoformat(),
            "banco": requerido(d, "ultimo_deposito_banco"),
        },
    }


def _guardar_liquidacion(conn, emp, liq: Liquidacion, pago: dict) -> int:
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


def liquidar_uno(conn, tipo: str, d: dict) -> tuple[int, Liquidacion]:
    emp = conn.execute("SELECT * FROM empleados WHERE id = ?", (requerido(d, "empleado_id"),)).fetchone()
    if emp is None:
        raise ErrorDatos("El empleado no existe")
    pago = datos_pago(d, empresa(conn, emp["empresa_id"]))
    liq = FUNCIONES[tipo](conn, emp, periodo(d), d)
    liq_id = _guardar_liquidacion(conn, emp, liq, pago)
    conn.commit()
    return liq_id, liq


def empleados_del_periodo(conn, empresa_id: int, tipo: str, periodo_: str) -> list:
    """Los que trabajaron al menos un día en el mes (o en el semestre, para el SAC)."""
    desde, hasta = semestre_de(periodo_) if tipo == "sac" else (primer_dia(periodo_), ultimo_dia(periodo_))
    return conn.execute(
        """SELECT * FROM empleados e WHERE empresa_id = ? AND fecha_ingreso <= ?
             AND (fecha_egreso IS NULL OR fecha_egreso >= ?)
             AND NOT EXISTS (SELECT 1 FROM liquidaciones f WHERE f.empleado_id = e.id
                             AND f.tipo = 'final' AND f.periodo BETWEEN ? AND ?)
           ORDER BY apellido, nombre""",
        (empresa_id, hasta.isoformat(), desde.isoformat(), desde.strftime("%Y-%m"),
         hasta.strftime("%Y-%m"))).fetchall()


def liquidar_empresa(conn, empresa_id: int, tipo: str, d: dict,
                     por_empleado: dict | None = None) -> tuple[str, list, list]:
    """Liquida el mes (o el SAC) a todos los que trabajaron en el período (o semestre).

    `por_empleado` ({empleado_id: {campo: valor}}) pisa los datos generales para
    cada uno, por ejemplo las inasistencias.
    """
    emp_empresa = empresa(conn, empresa_id)
    if emp_empresa is None:
        raise ErrorDatos("La empresa no existe")
    periodo_ = periodo(d)
    pago = datos_pago(d, emp_empresa)
    hechas, errores = [], []
    for emp in empleados_del_periodo(conn, empresa_id, tipo, periodo_):
        nombre = f"{emp['apellido']}, {emp['nombre']}"
        try:
            liq = FUNCIONES[tipo](conn, emp, periodo_, {**d, **(por_empleado or {}).get(emp["id"], {})})
        except ErrorDatos as exc:
            errores.append({"empleado_id": emp["id"], "legajo": emp["legajo"], "empleado": nombre,
                            "error": str(exc)})
            continue
        hechas.append({"id": _guardar_liquidacion(conn, emp, liq, pago), "empleado_id": emp["id"],
                       "legajo": emp["legajo"], "empleado": nombre, "neto": str(liq.neto)})
    conn.commit()
    return periodo_, hechas, errores


# --- Recibos -----------------------------------------------------------------
_SELECT_RECIBO = """SELECT l.*, e.apellido, e.nombre, e.cuil, e.fecha_ingreso, e.convenio, e.legajo,
                           x.razon_social, x.cuit, x.domicilio
                    FROM liquidaciones l
                    JOIN empleados e ON e.id = l.empleado_id
                    JOIN empresas x ON x.id = e.empresa_id"""


def _datos_recibo(row) -> DatosRecibo:
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


def _pdf(rows) -> bytes:
    buf = BytesIO()
    generar_recibos(buf, [_datos_recibo(r) for r in rows])
    return buf.getvalue()


def pdf_recibo(conn, liq_id: int) -> tuple[bytes, str] | None:
    row = conn.execute(_SELECT_RECIBO + " WHERE l.id = ?", (liq_id,)).fetchone()
    if row is None:
        return None
    return _pdf([row]), f"recibo_{row['cuil']}_{row['periodo']}_{row['tipo']}.pdf"


def pdf_recibos_empresa(conn, empresa_id: int, periodo_: str, tipo: str) -> tuple[bytes, str] | None:
    rows = conn.execute(
        _SELECT_RECIBO + " WHERE e.empresa_id = ? AND l.periodo = ? AND l.tipo = ? ORDER BY e.apellido, e.nombre",
        (empresa_id, periodo_, tipo)).fetchall()
    if not rows:
        return None
    return _pdf(rows), f"recibos_{rows[0]['cuit']}_{periodo_}_{tipo}.pdf"
