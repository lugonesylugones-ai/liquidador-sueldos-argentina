"""Operaciones del liquidador, compartidas por la API JSON y las pantallas web.

Cada función recibe la conexión y un dict con los datos (JSON o formulario) y
lanza `ErrorDatos` con un mensaje para el usuario si algo no cierra.
"""
import json
import sqlite3
from datetime import date
from decimal import Decimal, InvalidOperation
from io import BytesIO

from .calculo import (CAUSAS_EGRESO, Liquidacion, aplicar_descuentos_varios, dias_del_mes, liquidar_comercio, liquidar_final,
                      liquidar_sac, primer_dia, semestre_de, ultimo_dia)
from .calculo_suteryh import (ADICIONALES, BASES_ZONA, CARGO_POR_NOMBRE, CONVENIO_SUTERYH, TAREAS, fila_basico,
                              liquidar_sac_suteryh, liquidar_suteryh, liquidar_zona_fria, nombres_escala)
from . import arca
from . import conceptos as cp
from .cuit import normalizar_cuit
from .empleados import leer_empleados, validar_categoria
from .formato import pesos
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
    return [{**dict(r), "extras": extras(r), "arca": arca_empleado(r), "descuentos": descuentos_empleado(r),
             "conceptos": conceptos_empleado(r)}
            for r in conn.execute(
        "SELECT * FROM empleados WHERE empresa_id = ? ORDER BY fecha_egreso IS NOT NULL, apellido, nombre",
        (empresa_id,))]


# Datos propios de los encargados de edificio (CCT 589/10), con sus valores por defecto.
EXTRAS_SUTERYH = {"afiliado": True, "retira_residuos": False, "tareas": [], "tramos_titulo": 0,
                  "antiguedad_completa": False}


def extras(emp) -> dict:
    """Datos del empleado propios de su convenio (columna `extras`)."""
    guardados = json.loads(emp["extras"]) if emp["extras"] else {}
    guardados.pop("arca", None)   # los datos para el F.931 van aparte (arca_empleado)
    guardados.pop("descuentos", None)   # y los descuentos varios (descuentos_empleado)
    guardados.pop("conceptos", None)    # y los conceptos propios asignados (conceptos_empleado)
    return {**EXTRAS_SUTERYH, **guardados} if emp["convenio"] == CONVENIO_SUTERYH else guardados


def edificio(conn, empresa_id: int) -> dict | None:
    r = conn.execute("SELECT * FROM edificios WHERE empresa_id = ?", (empresa_id,)).fetchone()
    if r is None:
        return None
    return {**dict(r), "zona_desfavorable": bool(r["zona_desfavorable"]),
            "zona_recibo_aparte": bool(r["zona_recibo_aparte"])}


def guardar_edificio(conn, empresa_id: int, d: dict) -> None:
    """Categoría (1 a 4), unidades funcionales y zona fría / desfavorable del consorcio.

    `zona_base`: 'remunerativo' (todo lo remunerativo) o 'basico_antiguedad'.
    `zona_recibo_aparte`: la zona va en un recibo propio, con sus aportes y su redondeo.
    """
    if empresa(conn, empresa_id) is None:
        raise ErrorDatos("La empresa no existe")
    categoria = entero(d, "categoria", 0)
    if categoria not in (1, 2, 3, 4):
        raise ErrorDatos("La categoría del edificio tiene que ser 1, 2, 3 o 4")
    uf = entero(d, "unidades_funcionales", 0)
    if uf < 0:
        raise ErrorDatos("Las unidades funcionales no pueden ser negativas")
    zona_base = d.get("zona_base") or "remunerativo"
    if zona_base not in BASES_ZONA:
        raise ErrorDatos(f"'zona_base' tiene que ser {' o '.join(BASES_ZONA)}")
    conn.execute(
        """INSERT INTO edificios (empresa_id, categoria, unidades_funcionales, zona_desfavorable, zona_base,
                                 zona_recibo_aparte)
           VALUES (?, ?, ?, ?, ?, ?)
           ON CONFLICT (empresa_id) DO UPDATE SET categoria = excluded.categoria,
             unidades_funcionales = excluded.unidades_funcionales,
             zona_desfavorable = excluded.zona_desfavorable, zona_base = excluded.zona_base,
             zona_recibo_aparte = excluded.zona_recibo_aparte""",
        (empresa_id, categoria, uf, int(si_no(d, "zona_desfavorable", False)), zona_base,
         int(si_no(d, "zona_recibo_aparte", False))))
    conn.commit()


def si_no(d: dict, campo: str, default: bool) -> bool:
    valor = d.get(campo)
    if valor in (None, ""):
        return default
    if isinstance(valor, bool):
        return valor
    return str(valor).strip().lower() in ("1", "true", "si", "sí", "on")


def _lista(d, campo: str) -> list:
    if hasattr(d, "getlist"):  # formulario web
        return [v for v in d.getlist(campo) if v]
    valor = d.get(campo) or []
    return [valor] if isinstance(valor, str) else list(valor)


def _extras_suteryh(d: dict) -> dict:
    tareas = _lista(d, "tareas")
    desconocidas = [t for t in tareas if t not in TAREAS]
    if desconocidas:
        raise ErrorDatos(f"Tarea desconocida: {', '.join(desconocidas)}. Valen: {', '.join(TAREAS)}")
    tramos = entero(d, "tramos_titulo", 0)
    if not 0 <= tramos <= 3:
        raise ErrorDatos("'tramos_titulo' va de 0 a 3")
    return {"afiliado": si_no(d, "afiliado", True), "retira_residuos": si_no(d, "retira_residuos", False),
            "tareas": list(dict.fromkeys(tareas)), "tramos_titulo": tramos,
            "antiguedad_completa": si_no(d, "antiguedad_completa", False)}


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
                                  fecha_ingreso, jornada_horas, fecha_egreso, extras)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT (empresa_id, cuil) DO UPDATE SET
             legajo = excluded.legajo, apellido = excluded.apellido, nombre = excluded.nombre,
             convenio = excluded.convenio, categoria = excluded.categoria,
             fecha_ingreso = excluded.fecha_ingreso, jornada_horas = excluded.jornada_horas,
             fecha_egreso = excluded.fecha_egreso, extras = COALESCE(excluded.extras, empleados.extras)
           RETURNING id""",
        (empresa_id, f["legajo"], f["apellido"], f["nombre"], f["cuil"], f["convenio"], f["categoria"],
         f["fecha_ingreso"], _jornada(f), f["fecha_egreso"], f.get("extras")))
    return cur.fetchone()[0]


def _jornada(f: dict) -> int:
    """En edificios la media jornada es un cargo con su propia escala: la jornada sale del cargo."""
    if f["convenio"] == CONVENIO_SUTERYH:
        return 4 if CARGO_POR_NOMBRE[f["categoria"]][2] else 8
    return f["jornada_horas"]


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
            "fecha_egreso": egreso.isoformat() if egreso else None,
            "extras": json.dumps({**(_extras_suteryh(d) if convenio == CONVENIO_SUTERYH else {}),
                                  "arca": _extras_arca(d), "descuentos": _extras_descuentos(d),
                                  "conceptos": _extras_conceptos(conn, d, convenio)})})
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


def categorias_de(conn, convenio: str) -> list:
    cats = categorias(conn)
    if convenio not in cats:
        raise ErrorDatos(f"El convenio {convenio} no existe")
    return cats[convenio]


def filas_de_escala(conn, convenio: str) -> list:
    """Filas que acepta la plantilla de escala: las categorías, o en edificios cargo × categoría
    del edificio más los adicionales de la planilla."""
    cats = categorias_de(conn, convenio)
    return nombres_escala() if convenio == CONVENIO_SUTERYH else cats


def importar_escala(conn, contenido: bytes, nombre_archivo: str, convenio: str = CONVENIO_COMERCIO) -> int:
    cats = filas_de_escala(conn, convenio)
    if not cats:
        raise ErrorDatos(f"El convenio {convenio} no tiene categorías: cargalas primero")
    res = leer_plantilla(contenido, None if convenio == CONVENIO_COMERCIO else cats)
    if not res.ok:
        # No se carga nada si hay una sola fila mal: evita escalas a medias.
        raise ErrorDatos("La plantilla tiene errores, no se cargó nada", res.errores)
    return guardar_escala(conn, res.filas, convenio, fuente=f"Importada de {nombre_archivo}")


def agregar_categorias(conn, convenio: str, nombres: list) -> int:
    existentes = categorias_de(conn, convenio)
    nuevas = [n for n in dict.fromkeys(" ".join(n.split()) for n in nombres) if n and n not in existentes]
    for orden, nombre in enumerate(nuevas, start=len(existentes)):
        conn.execute("INSERT INTO categorias (convenio, nombre, orden) VALUES (?, ?, ?)", (convenio, nombre, orden))
    conn.commit()
    return len(nuevas)


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
    ingreso = date.fromisoformat(emp["fecha_ingreso"])
    egreso = date.fromisoformat(emp["fecha_egreso"]) if emp["fecha_egreso"] else None
    dias = dias_del_mes(periodo_, ingreso, egreso)
    if dias == 0:
        raise ErrorDatos(f"No trabajó en {periodo_}")
    if emp["convenio"] == CONVENIO_SUTERYH:
        return _mensual_suteryh(conn, emp, periodo_, d, ingreso, dias)
    escala, extraordinaria = _escala_del_mes(conn, emp, periodo_, d)
    try:
        return liquidar_comercio(
            conceptos_extra=_hook_conceptos(definiciones_para(conn, emp, periodo_, d)),
            periodo=periodo_, categoria=emp["categoria"], basico=escala.monto,
            no_remunerativo=escala.no_remunerativo, vigencia_escala=escala.vigencia_desde,
            fecha_ingreso=ingreso, jornada_horas=emp["jornada_horas"],
            asignacion_extraordinaria=extraordinaria, dias=dias,
            inasistencias_injustificadas=entero(d, "inasistencias_injustificadas", 0),
            tope_base_imponible=decimal_opcional(d, "tope_base_imponible"))
    except ValueError as exc:
        raise ErrorDatos(str(exc))


def _mensual_suteryh(conn, emp, periodo_: str, d: dict, ingreso: date, dias: int) -> Liquidacion:
    if entero(d, "inasistencias_injustificadas", 0):
        raise ErrorDatos("Las faltas de encargados de edificio todavía no se descuentan: "
                         "liquidalo sin faltas y ajustalo a mano")
    ed = edificio(conn, emp["empresa_id"])
    if ed is None:
        raise ErrorDatos("Faltan los datos del edificio (categoría y unidades funcionales) en la empresa")
    al = primer_dia(periodo_)
    fila = basico_vigente(conn, fila_basico(emp["categoria"], ed["categoria"]), al, CONVENIO_SUTERYH)
    if fila is None:
        raise ErrorDatos(f"No hay escala cargada para {emp['categoria']} ({ed['categoria']}ª cat.) "
                         f"vigente en {periodo_}")
    adicionales = {}
    for clave, nombre in ADICIONALES.items():
        a = basico_vigente(conn, nombre, al, CONVENIO_SUTERYH)
        if a is not None:
            adicionales[clave] = a.monto
    ex = extras(emp)
    try:
        return liquidar_suteryh(
            periodo=periodo_, cargo=emp["categoria"], categoria_edificio=ed["categoria"], basico=fila.monto,
            adicionales=adicionales, vigencia_escala=fila.vigencia_desde, fecha_ingreso=ingreso, dias=dias,
            unidades_funcionales=ed["unidades_funcionales"] if ex["retira_residuos"] else 0,
            tareas=ex["tareas"], tramos_titulo=ex["tramos_titulo"],
            zona_desfavorable=ed["zona_desfavorable"] and not ed["zona_recibo_aparte"], zona_base=ed["zona_base"],
            horas_50=decimal_opcional(d, "horas_50") or Decimal("0"),
            horas_100=decimal_opcional(d, "horas_100") or Decimal("0"),
            afiliado=ex["afiliado"], alicuota_sindical=decimal_opcional(d, "alicuota_sindical"),
            tope_base_imponible=decimal_opcional(d, "tope_base_imponible"),
            antiguedad_completa=ex["antiguedad_completa"],
            conceptos_extra=_hook_conceptos(definiciones_para(conn, emp, periodo_, d)))
    except ValueError as exc:
        raise ErrorDatos(str(exc))


def _sac_zona_fria(conn, emp, sac: Liquidacion, d: dict) -> Liquidacion | None:
    """SAC de la zona fría pagada en recibos aparte: mejor zona del semestre / 2, en su propio recibo
    (como la hoja SAC de la planilla de los consorcios)."""
    inicio, fin = semestre_de(sac.periodo)
    meses = [z for z in _historial(conn, emp["id"], "zona_fria") if inicio <= primer_dia(z.periodo) <= fin]
    if not meses:
        return None
    try:
        liq = liquidar_sac_suteryh(
            periodo=sac.periodo, categoria=emp["categoria"], fecha_ingreso=date.fromisoformat(emp["fecha_ingreso"]),
            fecha_egreso=date.fromisoformat(emp["fecha_egreso"]) if emp["fecha_egreso"] else None,
            jornada_horas=emp["jornada_horas"], historial=meses, afiliado=extras(emp)["afiliado"],
            alicuota_sindical=decimal_opcional(d, "alicuota_sindical"),
            tope_base_imponible=decimal_opcional(d, "tope_base_imponible"))
    except ValueError as exc:
        raise ErrorDatos(str(exc))
    for c in liq.conceptos:
        if c.codigo == "SAC":
            c.descripcion = "SAC s/ zona fría"
    liq.tipo = "sac_zona_fria"
    return liq


def _zona_fria_aparte(conn, emp, mensual: Liquidacion, d: dict) -> Liquidacion | None:
    """Recibo aparte de zona fría para el sueldo de un encargado, si el consorcio lo liquida así."""
    if emp["convenio"] != CONVENIO_SUTERYH:
        return None
    if mensual.tipo == "sac":
        return _sac_zona_fria(conn, emp, mensual, d)
    if mensual.tipo != "mensual":
        return None
    ed = edificio(conn, emp["empresa_id"])
    if not (ed and ed["zona_desfavorable"] and ed["zona_recibo_aparte"]):
        return None
    pct = basico_vigente(conn, ADICIONALES["zona_desfavorable_pct"], primer_dia(mensual.periodo), CONVENIO_SUTERYH)
    if pct is None:
        raise ErrorDatos(f"No hay porcentaje de zona desfavorable en la escala vigente en {mensual.periodo}")
    liq = liquidar_zona_fria(mensual, porcentaje=pct.monto, zona_base=ed["zona_base"],
                             afiliado=extras(emp)["afiliado"],
                             alicuota_sindical=decimal_opcional(d, "alicuota_sindical"),
                             tope_base_imponible=decimal_opcional(d, "tope_base_imponible"))
    liq.tipo = "zona_fria"
    return liq


def _guardar_mes(conn, emp, liq: Liquidacion, pago: dict, d: dict) -> tuple[int, Liquidacion | None]:
    """Guarda la liquidación y, si corresponde, el recibo aparte de zona fría (o de su SAC)."""
    zona = _zona_fria_aparte(conn, emp, liq, d)
    _descontar(conn, emp, liq, d)
    if zona is not None:
        _descontar(conn, emp, zona, d)
    liq_id = _guardar_liquidacion(conn, emp, liq, pago)
    tipo_zona = {"mensual": "zona_fria", "sac": "sac_zona_fria"}.get(liq.tipo)
    if zona is not None:
        _guardar_liquidacion(conn, emp, zona, pago)
    elif tipo_zona:  # el consorcio dejó de liquidarla aparte: no queda un recibo viejo colgado
        conn.execute("DELETE FROM liquidaciones WHERE empleado_id = ? AND periodo = ? AND tipo = ?",
                     (emp["id"], liq.periodo, tipo_zona))
    return liq_id, zona


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


def _historial(conn, empleado_id: int, tipo: str = "mensual") -> list:
    """Liquidaciones del empleado de un tipo, como meses para el SAC (`tipo` 'zona_fria': los
    recibos aparte de zona fría, que tienen su propio SAC)."""
    rows = conn.execute("SELECT resultado FROM liquidaciones WHERE empleado_id = ? AND tipo = ?",
                        (empleado_id, tipo)).fetchall()
    meses = [Liquidacion.from_dict(json.loads(r["resultado"])["liquidacion"]) for r in rows]
    for m in meses:
        m.tipo = "mensual"
    return meses


def liquidar_aguinaldo(conn, emp, periodo_: str, d: dict) -> Liquidacion:
    _exigir_motor(conn, emp)
    egreso_en_mes = bool(emp["fecha_egreso"]) and emp["fecha_egreso"][:7] == periodo_
    if int(periodo_[5:]) not in (6, 12) and not egreso_en_mes:
        raise ErrorDatos("El SAC se liquida en junio, en diciembre o en el mes del egreso")
    inicio, fin = semestre_de(periodo_)
    final = _final_entre(conn, emp["id"], inicio.strftime("%Y-%m"), fin.strftime("%Y-%m"))
    if final:
        raise ErrorDatos(f"El SAC proporcional ya se pagó en la liquidación final de {final['periodo']}")
    datos = dict(
        periodo=periodo_, categoria=emp["categoria"],
        fecha_ingreso=date.fromisoformat(emp["fecha_ingreso"]),
        fecha_egreso=date.fromisoformat(emp["fecha_egreso"]) if emp["fecha_egreso"] else None,
        jornada_horas=emp["jornada_horas"], historial=_historial(conn, emp["id"]),
        tope_base_imponible=decimal_opcional(d, "tope_base_imponible"))
    try:
        if emp["convenio"] == CONVENIO_SUTERYH:
            return liquidar_sac_suteryh(**datos, afiliado=extras(emp)["afiliado"],
                                        alicuota_sindical=decimal_opcional(d, "alicuota_sindical"))
        return liquidar_sac(**datos)
    except ValueError as exc:
        raise ErrorDatos(str(exc))


FUNCIONES = {"mensual": liquidar_mensual, "sac": liquidar_aguinaldo}


def liquidar_egreso(conn, empleado_id: int, d: dict) -> tuple[int, Liquidacion]:
    """Liquidación final: guarda la fecha de egreso y reemplaza el sueldo y el SAC de ese mes."""
    emp = conn.execute("SELECT * FROM empleados WHERE id = ?", (empleado_id,)).fetchone()
    if emp is None:
        raise ErrorDatos("El empleado no existe")
    _exigir_motor(conn, emp)
    if emp["convenio"] == CONVENIO_SUTERYH:
        raise ErrorDatos("La liquidación final de encargados de edificio todavía no está programada "
                         "(el CCT 589/10 cuenta las vacaciones en días hábiles)")
    causa = requerido(d, "causa")
    if causa not in CAUSAS_EGRESO:
        raise ErrorDatos(f"Causa de egreso desconocida: {causa}")
    egreso = fecha(d, "fecha_egreso")
    ingreso = date.fromisoformat(emp["fecha_ingreso"])
    if egreso < ingreso:
        raise ErrorDatos("La fecha de egreso es anterior a la de ingreso")
    periodo_ = egreso.strftime("%Y-%m")
    _exigir_abierto(conn, emp["empresa_id"], periodo_)
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
            tope_base_imponible=decimal_opcional(d, "tope_base_imponible"),
            conceptos_extra=_hook_conceptos(definiciones_para(conn, emp, periodo_, d)))
    except ValueError as exc:
        raise ErrorDatos(str(exc))
    _descontar(conn, emp, liq, d)
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


def periodos_cerrados(conn, empresa_id: int) -> set:
    return {r["periodo"] for r in conn.execute(
        "SELECT periodo FROM periodos_cerrados WHERE empresa_id = ?", (empresa_id,))}


def _exigir_abierto(conn, empresa_id: int, periodo_: str) -> None:
    if periodo_ in periodos_cerrados(conn, empresa_id):
        raise ErrorDatos(f"El período {periodo_} está cerrado (ya presentado). Reabrilo para volver a liquidarlo")


def cerrar_periodo(conn, empresa_id: int, periodo_: str, cerrar: bool = True) -> None:
    """Cierra un período ya presentado (o lo reabre): cerrado, no se puede volver a liquidar."""
    if empresa(conn, empresa_id) is None:
        raise ErrorDatos("La empresa no existe")
    periodo({"periodo": periodo_})
    if cerrar:
        conn.execute("INSERT OR IGNORE INTO periodos_cerrados (empresa_id, periodo) VALUES (?, ?)",
                     (empresa_id, periodo_))
    else:
        conn.execute("DELETE FROM periodos_cerrados WHERE empresa_id = ? AND periodo = ?", (empresa_id, periodo_))
    conn.commit()


def _guardar_liquidacion(conn, emp, liq: Liquidacion, pago: dict) -> int:
    _exigir_abierto(conn, emp["empresa_id"], liq.periodo)
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
    liq_id, _ = _guardar_mes(conn, emp, liq, pago, d)
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
    _exigir_abierto(conn, empresa_id, periodo_)
    pago = datos_pago(d, emp_empresa)
    hechas, errores = [], []
    for emp in empleados_del_periodo(conn, empresa_id, tipo, periodo_):
        nombre = f"{emp['apellido']}, {emp['nombre']}"
        datos = {**d, **(por_empleado or {}).get(emp["id"], {})}
        try:
            liq = FUNCIONES[tipo](conn, emp, periodo_, datos)
            liq_id, zona = _guardar_mes(conn, emp, liq, pago, datos)
        except ErrorDatos as exc:
            errores.append({"empleado_id": emp["id"], "legajo": emp["legajo"], "empleado": nombre,
                            "error": str(exc)})
            continue
        hechas.append({"id": liq_id, "empleado_id": emp["id"], "legajo": emp["legajo"], "empleado": nombre,
                       "neto": str(liq.neto), **({"neto_zona_fria": str(zona.neto)} if zona else {})})
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


def resumen_general(conn) -> dict:
    """Números del tablero: empresas, empleados activos y el último mes liquidado."""
    r = conn.execute("""SELECT (SELECT COUNT(*) FROM empresas) AS empresas,
                               (SELECT COUNT(*) FROM empleados WHERE fecha_egreso IS NULL) AS activos""").fetchone()
    ultimo = conn.execute("SELECT MAX(periodo) FROM liquidaciones WHERE tipo = 'mensual'").fetchone()[0]
    # El neto del mes incluye la zona fría que se paga en recibo aparte.
    neto = Decimal("0")
    recibos = 0
    if ultimo:
        for f in conn.execute("SELECT resultado FROM liquidaciones WHERE tipo IN ('mensual', 'zona_fria') "
                              "AND periodo = ?", (ultimo,)):
            neto += Decimal(json.loads(f["resultado"])["liquidacion"]["neto"])
            recibos += 1
    return {"empresas": r["empresas"], "activos": r["activos"], "ultimo_periodo": ultimo,
            "neto_ultimo": neto, "recibos_ultimo": recibos,
            "convenios": conn.execute("SELECT COUNT(*) FROM convenios").fetchone()[0]}


# --- Descuentos varios ----------------------------------------------------------
# Tipo -> (código del recibo, descripción). No salen del convenio: los carga quien liquida.
TIPOS_DESCUENTO = {"mutual": ("DMUT", "Mutual"), "embargo": ("DEMB", "Embargo judicial"),
                   "prestamo": ("DPRE", "Cuota préstamo"), "otro": ("DOTR", "Otro descuento")}
FILAS_DESCUENTO = 3   # cuántos descuentos fijos se cargan por empleado desde la pantalla


def descuentos_empleado(emp) -> list:
    return (json.loads(emp["extras"]) if emp["extras"] else {}).get("descuentos", [])


def _descuento(d: dict, n: int | None = None) -> dict | None:
    """Un descuento fijo: importe por mes o porcentaje (con un mínimo no embargable)."""
    def campo(nombre):
        return d.get(f"descuento_{nombre}_{n}") if n is not None else d.get(nombre)
    tipo = (campo("tipo") or "").strip()
    importe = decimal_opcional({"importe": campo("importe")}, "importe")
    porcentaje = decimal_opcional({"porcentaje": campo("porcentaje")}, "porcentaje")
    if not tipo and importe is None and porcentaje is None:
        return None
    if tipo not in TIPOS_DESCUENTO:
        raise ErrorDatos(f"Tipo de descuento desconocido: {tipo or '(vacío)'}. Valen: {', '.join(TIPOS_DESCUENTO)}")
    if (importe is None) == (porcentaje is None):
        raise ErrorDatos("Cada descuento lleva un importe fijo o un porcentaje, no los dos")
    if (importe is not None and importe <= 0) or (porcentaje is not None and not 0 < porcentaje <= 100):
        raise ErrorDatos("El importe tiene que ser mayor a cero y el porcentaje entre 0 y 100")
    minimo = decimal_opcional({"minimo": campo("minimo")}, "minimo") or Decimal("0")
    hasta = (campo("hasta") or "").strip()
    if hasta:
        try:
            primer_dia(hasta)
        except ValueError:
            raise ErrorDatos("'hasta' es el último período del descuento, AAAA-MM")
    out = {"tipo": tipo, "detalle": (campo("detalle") or "").strip()[:60], "hasta": hasta}
    if importe is not None:
        out["importe"] = str(importe)
    else:
        out["porcentaje"], out["minimo"] = str(porcentaje), str(minimo)
    return out


def _extras_descuentos(d: dict) -> list:
    if isinstance(d.get("descuentos"), list):   # API JSON
        return [x for x in (_descuento(item) for item in d["descuentos"]) if x]
    return [x for x in (_descuento(d, n) for n in range(1, FILAS_DESCUENTO + 1)) if x]


def _descontar(conn, emp, liq: Liquidacion, d: dict) -> None:
    """Descuentos fijos del empleado y el anticipo del mes. Los importes fijos van en el sueldo
    (o la final); los porcentajes, en cada recibo, y el mínimo no embargable solo en el sueldo."""
    principal = liq.tipo in ("mensual", "final")
    base = liq.total_remunerativo + liq.total_no_remunerativo - sum(
        (c.importe for c in liq.conceptos if c.codigo == "RED"), Decimal("0"))
    items = []
    for x in descuentos_empleado(emp):
        if x.get("hasta") and liq.periodo > x["hasta"]:
            continue
        codigo, nombre = TIPOS_DESCUENTO[x["tipo"]]
        descripcion = f"{nombre} {x['detalle']}".strip()
        if "importe" in x:
            if principal:
                items.append((codigo, descripcion, "Importe fijo mensual", Decimal(x["importe"])))
            continue
        pct, minimo = Decimal(x["porcentaje"]), Decimal(x["minimo"]) if principal else Decimal("0")
        sujeto = max(base - minimo, Decimal("0"))
        detalle = f"{pesos(pct)}% s/ $ {pesos(sujeto)}" + (f" (bruto - $ {pesos(minimo)})" if minimo else "")
        items.append((codigo, descripcion, detalle, sujeto * pct / 100))
    anticipo = decimal_opcional(d, "anticipo")
    if anticipo and principal:
        items.append(("DANT", "Anticipo de haberes", "Entregado a cuenta", anticipo))
    try:
        items += cp.descuentos(liq, definiciones_para(conn, emp, liq.periodo, d), principal=principal)
        aplicar_descuentos_varios(liq, items)
    except ValueError as exc:
        raise ErrorDatos(str(exc))


# --- ARCA: Libro de Sueldos Digital y F.931 ------------------------------------
def datos_arca(conn, empresa_id: int) -> dict:
    """Códigos del empleador para el F.931; si no se cargaron, los valores por defecto."""
    r = conn.execute("SELECT * FROM datos_arca WHERE empresa_id = ?", (empresa_id,)).fetchone()
    por_defecto = {"tipo_empleador": arca.TIPO_EMPLEADOR, "actividad": arca.ACTIVIDAD, "zona": arca.ZONA}
    return {**por_defecto, **({k: r[k] for k in por_defecto} if r else {}), "cargados": r is not None}


def guardar_datos_arca(conn, empresa_id: int, d: dict) -> None:
    if empresa(conn, empresa_id) is None:
        raise ErrorDatos("La empresa no existe")
    valores = {}
    for campo, largo in (("tipo_empleador", 1), ("actividad", 3), ("zona", 2)):
        valor = str(requerido(d, campo)).strip()
        if not valor.isdigit() or len(valor) > largo:
            raise ErrorDatos(f"'{campo}' tiene que ser un código de hasta {largo} dígitos")
        valores[campo] = valor.zfill(largo)
    conn.execute(
        """INSERT INTO datos_arca (empresa_id, tipo_empleador, actividad, zona) VALUES (?, ?, ?, ?)
           ON CONFLICT (empresa_id) DO UPDATE SET tipo_empleador = excluded.tipo_empleador,
             actividad = excluded.actividad, zona = excluded.zona""",
        (empresa_id, valores["tipo_empleador"], valores["actividad"], valores["zona"]))
    conn.commit()


def arca_empleado(emp) -> dict:
    """Datos del trabajador para el F.931 (obra social, cónyuge, hijos, CBU), con sus defaults."""
    guardados = (json.loads(emp["extras"]) if emp["extras"] else {}).get("arca", {})
    return {"obra_social": arca.OBRA_SOCIAL.get(emp["convenio"], ""), "conyuge": False, "hijos": 0, "cbu": "",
            **guardados}


def _extras_arca(d: dict) -> dict:
    obra_social = (d.get("obra_social") or "").strip()
    if obra_social and (not obra_social.isdigit() or len(obra_social) > 6):
        raise ErrorDatos("El código de obra social tiene hasta 6 dígitos (ej. 126205 OSECAC)")
    cbu = "".join(c for c in (d.get("cbu") or "") if c.isdigit())
    if cbu and len(cbu) != 22:
        raise ErrorDatos("La CBU tiene 22 dígitos")
    hijos = entero(d, "hijos", 0)
    if not 0 <= hijos <= 99:
        raise ErrorDatos("'hijos' va de 0 a 99")
    datos = {"conyuge": si_no(d, "conyuge", False), "hijos": hijos, "cbu": cbu}
    if obra_social:
        datos["obra_social"] = obra_social.zfill(6)
    return datos


def _conceptos_del_periodo(conn, emp, periodo_: str) -> tuple[list, dict, date, int]:
    """Suma por código los conceptos de todos los recibos del trabajador en el período
    (sueldo, zona fría aparte, aguinaldo, liquidación final): el F.931 es uno por mes."""
    filas = conn.execute("SELECT tipo, fecha_pago, resultado FROM liquidaciones WHERE empleado_id = ? "
                         "AND periodo = ?", (emp["id"], periodo_)).fetchall()
    sumas, cantidades, dias = {}, {}, 30
    pago = max(date.fromisoformat(f["fecha_pago"]) for f in filas)
    mes = int(periodo_[5:])
    for f in filas:
        liq = Liquidacion.from_dict(json.loads(f["resultado"])["liquidacion"])
        if f["tipo"] in ("mensual", "final"):
            dias = min(liq.dias_trabajados, 30)
        for c in liq.conceptos:
            codigo = c.codigo
            if codigo == "SAC" and (f["tipo"] == "final" or mes not in (6, 12)):
                # Fuera de junio y diciembre ARCA solo acepta el SAC proporcional, con sus días.
                codigo = "SACP"
                cantidades["SACP"] = _dias_sac(emp, periodo_, liq)
            if codigo.startswith("INAS"):
                cantidades[codigo] = cantidades.get(codigo, 0) + int(c.detalle.split()[0])
            if codigo in ("HE50", "HE100"):
                cantidades[codigo] = cantidades.get(codigo, Decimal("0")) + Decimal(
                    c.detalle.split()[0].replace(",", "."))
            clave = (codigo, c.tipo)
            sumas[clave] = sumas.get(clave, Decimal("0")) + c.importe
    conceptos = [(codigo, tipo, importe) for (codigo, tipo), importe in sumas.items()]
    return conceptos, cantidades, pago, dias


def _dias_sac(emp, periodo_: str, liq: Liquidacion) -> int:
    inicio, fin = semestre_de(periodo_)
    desde = max(inicio, date.fromisoformat(emp["fecha_ingreso"]))
    hasta = date.fromisoformat(liq.egreso["fecha"]) if liq.egreso else min(fin, ultimo_dia(periodo_))
    return (hasta - desde).days + 1


def archivo_arca(conn, empresa_id: int, periodo_: str) -> tuple[str, str]:
    """TXT de la liquidación del período para importar en el Libro de Sueldos Digital."""
    emp_empresa = empresa(conn, empresa_id)
    if emp_empresa is None:
        raise ErrorDatos("La empresa no existe")
    trabajadores = []
    for emp in conn.execute(
            """SELECT DISTINCT e.* FROM empleados e JOIN liquidaciones l ON l.empleado_id = e.id
               WHERE e.empresa_id = ? AND l.periodo = ? ORDER BY e.apellido, e.nombre""",
            (empresa_id, periodo_)):
        conceptos, cantidades, pago, dias = _conceptos_del_periodo(conn, emp, periodo_)
        datos = arca_empleado(emp)
        # Comercio en jornada parcial: la obra social va sobre la jornada completa (art. 92 ter LCT).
        factor = (Decimal(8) / emp["jornada_horas"]
                  if emp["convenio"] == CONVENIO_COMERCIO and emp["jornada_horas"] < 8 else Decimal("1"))
        trabajadores.append(arca.Trabajador(
            cuil=emp["cuil"], legajo=emp["legajo"] or "", jornada_horas=emp["jornada_horas"],
            obra_social=datos["obra_social"], fecha_pago=pago, conceptos=conceptos, dias_trabajados=dias,
            factor_obra_social=factor, conyuge=datos["conyuge"], hijos=datos["hijos"], cbu=datos["cbu"],
            cantidades=cantidades, propios=_propios_arca(conn, emp["convenio"])))
    if not trabajadores:
        raise ErrorDatos(f"No hay recibos liquidados en {periodo_}")
    codigos = datos_arca(conn, empresa_id)
    try:
        texto = arca.archivo_liquidacion(
            cuit_empleador=emp_empresa["cuit"], periodo=periodo_, trabajadores=trabajadores,
            tipo_empleador=codigos["tipo_empleador"], actividad=codigos["actividad"], zona=codigos["zona"])
    except arca.ErrorArca as exc:
        raise ErrorDatos(str(exc))
    cuit_ = "".join(c for c in emp_empresa["cuit"] if c.isdigit())
    return texto, f"LSD_{cuit_}_{periodo_.replace('-', '')}.txt"


def archivo_conceptos_arca(conn, empresa_id: int) -> tuple[str, str]:
    """TXT con la relación de conceptos del empleador con los de ARCA (se sube una vez)."""
    emp_empresa = empresa(conn, empresa_id)
    if emp_empresa is None:
        raise ErrorDatos("La empresa no existe")
    cuit_ = "".join(c for c in emp_empresa["cuit"] if c.isdigit())
    convenios_ = {r["convenio"] for r in conn.execute("SELECT DISTINCT convenio FROM empleados WHERE empresa_id = ?",
                                                      (empresa_id,))}
    convenio = CONVENIO_SUTERYH if convenios_ == {CONVENIO_SUTERYH} else None
    propios = {k: v for k, v in _propios_arca(conn, convenio).items()
               if concepto_propio(conn, k)["empresa_id"] in (None, empresa_id)}
    return (arca.archivo_conceptos(list(arca.CONCEPTOS) + list(propios), propios),
            f"LSD_conceptos_{cuit_}.txt")


# --- Conceptos propios ------------------------------------------------------------
FILAS_CONCEPTO = 3   # cuántos conceptos se asignan por empleado desde la pantalla
# Lo que no puede ser el código de un concepto propio: los del liquidador y las variables.
_RESERVADOS = set(arca.CONCEPTOS) | {"SACP", "HE50", "HE100"} | set(cp.VARIABLES)
_PREFIJOS_RESERVADOS = ("INAS", "SAC")


def conceptos_propios(conn, solo_activos: bool = False) -> list:
    sql = "SELECT * FROM conceptos" + (" WHERE activo = 1" if solo_activos else "") + " ORDER BY orden, codigo"
    return [{**dict(r), "base": [b for b in r["base"].split(",") if b]} for r in conn.execute(sql)]


def concepto_propio(conn, codigo: str):
    r = conn.execute("SELECT * FROM conceptos WHERE codigo = ?", (codigo,)).fetchone()
    return {**dict(r), "base": [b for b in r["base"].split(",") if b]} if r else None


def _codigos_conocidos(conn, sin: str = "") -> set:
    propios = {r["codigo"] for r in conn.execute("SELECT codigo FROM conceptos WHERE codigo <> ?", (sin,))}
    return set(arca.CONCEPTOS) | {"HE50", "HE100"} | propios


def guardar_concepto(conn, d: dict) -> str:
    """Alta o modificación de un concepto propio. El código no se cambia una vez creado."""
    codigo = (d.get("codigo") or "").strip().upper()
    if not cp.CODIGO_VALIDO.match(codigo):
        raise ErrorDatos("El código va en mayúsculas, empieza con una letra y tiene de 2 a 10 letras o números")
    if codigo in _RESERVADOS or codigo.startswith(_PREFIJOS_RESERVADOS):
        raise ErrorDatos(f"El código {codigo} ya lo usa el liquidador: elegí otro")
    tipo, calculo = d.get("tipo") or "", d.get("calculo") or ""
    if tipo not in cp.TIPOS:
        raise ErrorDatos(f"Tipo desconocido: {tipo or '(vacío)'}. Valen: {', '.join(cp.TIPOS)}")
    if calculo not in cp.CALCULOS:
        raise ErrorDatos(f"Cálculo desconocido: {calculo or '(vacío)'}. Valen: {', '.join(cp.CALCULOS)}")
    valor = decimal_opcional(d, "valor") or Decimal("0")
    if valor < 0:
        raise ErrorDatos("El valor no puede ser negativo")
    base = d.get("base") or []
    if isinstance(base, str):
        base = base.replace(";", ",").replace(" ", ",").split(",")
    base = [b.strip().upper() for b in base if b.strip()]
    formula = (d.get("formula") or "").strip()
    conocidos = _codigos_conocidos(conn, codigo) | set(cp.VARIABLES)
    if calculo == "porcentaje":
        if not base:
            raise ErrorDatos("Decí sobre qué conceptos se calcula el porcentaje (por ejemplo BAS, ANT)")
        if not 0 < valor <= 1000:
            raise ErrorDatos("El porcentaje tiene que ser mayor a 0")
    elif calculo == "formula":
        if not formula:
            raise ErrorDatos("Falta la fórmula")
        try:
            nombres = cp.nombres_de(formula)
        except cp.ErrorFormula as exc:
            raise ErrorDatos(str(exc))
        base = []
    usados = set(base) if calculo == "porcentaje" else (nombres if calculo == "formula" else set())
    if codigo in usados:
        raise ErrorDatos("Un concepto no se puede calcular sobre sí mismo")
    desconocidos = sorted(usados - conocidos)
    if desconocidos:
        raise ErrorDatos(f"No conozco {', '.join(desconocidos)}: usá códigos del recibo (BAS, ANT, PRES...), "
                         f"de otros conceptos tuyos o {', '.join(cp.VARIABLES)}")
    codigo_arca = str(d.get("arca") or "").strip()
    if not (codigo_arca.isdigit() and len(codigo_arca) == 6):
        raise ErrorDatos("El código ARCA tiene 6 dígitos (por ejemplo 160000 remunerativo, 540000 no "
                         "remunerativo, 820000 descuento): consultalo con el contador")
    convenio = (d.get("convenio") or "").strip() or None
    if convenio and convenio not in categorias(conn):
        raise ErrorDatos(f"El convenio {convenio} no existe")
    empresa_id = d.get("empresa_id") or None
    if empresa_id:
        empresa_id = int(empresa_id)
        if empresa(conn, empresa_id) is None:
            raise ErrorDatos("La empresa no existe")
    fila = (codigo, requerido(d, "descripcion").strip()[:60], tipo, calculo, str(valor), ",".join(base), formula,
            int(si_no(d, "proporcional", True)), int(si_no(d, "habitual", True)), int(si_no(d, "aportes", True)),
            codigo_arca, convenio, empresa_id, int(si_no(d, "automatico", False)), entero(d, "orden", 100),
            int(si_no(d, "activo", True)))
    conn.execute(
        """INSERT INTO conceptos (codigo, descripcion, tipo, calculo, valor, base, formula, proporcional, habitual,
                                  aportes, arca, convenio, empresa_id, automatico, orden, activo)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT (codigo) DO UPDATE SET descripcion = excluded.descripcion, tipo = excluded.tipo,
             calculo = excluded.calculo, valor = excluded.valor, base = excluded.base, formula = excluded.formula,
             proporcional = excluded.proporcional, habitual = excluded.habitual, aportes = excluded.aportes,
             arca = excluded.arca, convenio = excluded.convenio, empresa_id = excluded.empresa_id,
             automatico = excluded.automatico, orden = excluded.orden, activo = excluded.activo""", fila)
    conn.commit()
    return codigo


def conceptos_empleado(emp) -> list:
    return (json.loads(emp["extras"]) if emp["extras"] else {}).get("conceptos", [])


def _asignacion(d: dict, n: int | None = None) -> dict | None:
    """Un concepto propio asignado a un empleado, con su valor y cantidad si son distintos."""
    def campo(nombre):
        return d.get(f"concepto_{nombre}_{n}") if n is not None else d.get(nombre)
    codigo = (campo("codigo") or "").strip().upper()
    if not codigo:
        return None
    out = {"codigo": codigo}
    for nombre in ("valor", "cantidad"):
        v = decimal_opcional({nombre: campo(nombre)}, nombre)
        if v is not None:
            if v < 0:
                raise ErrorDatos(f"El {nombre} de {codigo} no puede ser negativo")
            out[nombre] = str(v)
    hasta = (campo("hasta") or "").strip()
    if hasta:
        try:
            primer_dia(hasta)
        except ValueError:
            raise ErrorDatos("'hasta' es el último período del concepto, AAAA-MM")
        out["hasta"] = hasta
    return out


def _extras_conceptos(conn, d: dict, convenio: str) -> list:
    if isinstance(d.get("conceptos"), list):   # API JSON
        asignados = [_asignacion(x) for x in d["conceptos"]]
    else:
        asignados = [_asignacion(d, n) for n in range(1, FILAS_CONCEPTO + 1)]
    asignados = [a for a in asignados if a]
    for a in asignados:
        c = concepto_propio(conn, a["codigo"])
        if c is None:
            raise ErrorDatos(f"No existe el concepto {a['codigo']}: crealo primero en Conceptos")
        if c["convenio"] and c["convenio"] != convenio:
            raise ErrorDatos(f"El concepto {a['codigo']} es del convenio {c['convenio']}")
    return asignados


def _cantidad_del_mes(d: dict, codigo: str):
    cantidades = d.get("cantidades") if isinstance(d.get("cantidades"), dict) else {}
    return decimal_opcional({"c": cantidades.get(codigo, d.get(f"cantidad_{codigo}"))}, "c")


def definiciones_para(conn, emp, periodo_: str, d: dict) -> list:
    """Los conceptos propios que le tocan al empleado en el período, con su valor y cantidad:
    los automáticos de su convenio y empresa, y los que tiene asignados (que pisan valor y cantidad)."""
    asignados = {a["codigo"]: a for a in conceptos_empleado(emp) if not a.get("hasta") or periodo_ <= a["hasta"]}
    defs = []
    for c in conceptos_propios(conn, solo_activos=True):
        alcance = (c["convenio"] in (None, emp["convenio"]) and c["empresa_id"] in (None, emp["empresa_id"]))
        a = asignados.get(c["codigo"])
        if a is None and not (c["automatico"] and alcance):
            continue
        a = a or {}
        cantidad = _cantidad_del_mes(d, c["codigo"])
        defs.append(cp.Definicion(
            codigo=c["codigo"], descripcion=c["descripcion"], tipo=c["tipo"], calculo=c["calculo"],
            valor=Decimal(a.get("valor", c["valor"])), base=tuple(c["base"]), formula=c["formula"],
            proporcional=bool(c["proporcional"]), habitual=bool(c["habitual"]), aportes=bool(c["aportes"]),
            cantidad=cantidad if cantidad is not None else Decimal(a.get("cantidad", "0"))))
    return defs


def _hook_conceptos(defs: list):
    """Función para los motores: agrega los haberes propios antes de los aportes."""
    if not any(x.tipo != "descuento" for x in defs):
        return None

    def agregar(liq, dias):
        try:
            cp.aplicar_haberes(liq, defs, dias=dias)
        except cp.ErrorFormula as exc:
            raise ValueError(str(exc))
    return agregar


def conceptos_con_cantidad(conn, emp, periodo_: str) -> list:
    """Conceptos del empleado que piden una cantidad por mes (para la pantalla de liquidar)."""
    return [x for x in definiciones_para(conn, emp, periodo_, {})
            if x.calculo == "cantidad" or (x.calculo == "formula" and "CANTIDAD" in cp.nombres_de(x.formula))]


def _propios_arca(conn, convenio: str | None) -> dict:
    """Conceptos propios para el Libro de Sueldos Digital. En edificios lo no remunerativo no paga
    obra social (el motor no la cobra), así que no suma a ninguna base."""
    return {c["codigo"]: arca.ConceptoArca(
        c["arca"], c["descripcion"],
        arca.bases_propio(c["tipo"], bool(c["aportes"]) and convenio != CONVENIO_SUTERYH))
        for c in conceptos_propios(conn)}
