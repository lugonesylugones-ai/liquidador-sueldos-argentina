"""Carga de escalas salariales desde la plantilla Excel propia.

La plantilla tiene una hoja "Escala" con estas columnas:
    Categoría | Monto | Vigencia desde | No remunerativo | Asig. única vez

Cada fila es el básico mensual de jornada completa de una categoría a partir
de una fecha, la suma no remunerativa del acuerdo para ese mes y la asignación
extraordinaria de única vez si la hay (las dos últimas opcionales, vacía = 0).
La asignación de única vez solo se paga en el mes exacto de su vigencia. Así se cargan las circulares de FAECYS, que traen por categoría y
por mes el básico y el "aumento no remunerativo". Para cargar un nuevo acuerdo se agregan filas con la nueva
vigencia; las anteriores quedan como historial.
"""
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from io import BytesIO
import sqlite3

from openpyxl import Workbook, load_workbook
from openpyxl.styles import Font, PatternFill

CONVENIO_COMERCIO = "CCT 130/75"
HOJA = "Escala"
COLUMNAS = ("Categoría", "Monto", "Vigencia desde", "No remunerativo", "Asig. única vez")
COLUMNAS_OBLIGATORIAS = COLUMNAS[:3]

# Categorías del CCT 130/75 (art. 5 y siguientes).
CATEGORIAS_COMERCIO = [
    "Maestranza A", "Maestranza B", "Maestranza C",
    "Administrativo A", "Administrativo B", "Administrativo C",
    "Administrativo D", "Administrativo E", "Administrativo F",
    "Cajero A", "Cajero B", "Cajero C",
    "Auxiliar A", "Auxiliar B", "Auxiliar C",
    "Auxiliar Especializado A", "Auxiliar Especializado B",
    "Vendedor A", "Vendedor B", "Vendedor C", "Vendedor D",
]


# Cómo aparecen las categorías en circulares y escalas publicadas.
_PREFIJOS = {
    "personal auxiliar especializado": "Auxiliar Especializado",
    "auxiliar especializado": "Auxiliar Especializado",
    "personal auxiliar": "Auxiliar",
    "auxiliares": "Auxiliar",
    "auxiliar": "Auxiliar",
    "vendedores": "Vendedor",
    "vendedor": "Vendedor",
    "cajeros": "Cajero",
    "cajero": "Cajero",
    "administrativos": "Administrativo",
    "administrativo": "Administrativo",
    "maestranza": "Maestranza",
}


def normalizar_categoria(texto) -> str | None:
    """'Personal Auxiliar B', 'VENDEDORES  A', 'Cajeros \"C\"' -> nombre del CCT, o None."""
    limpio = " ".join(str(texto or "").replace('"', " ").replace("'", " ").lower().split())
    for prefijo, nombre in _PREFIJOS.items():
        if limpio.startswith(prefijo + " "):
            letra = limpio[len(prefijo):].strip().upper()
            candidato = f"{nombre} {letra}"
            return candidato if candidato in CATEGORIAS_COMERCIO else None
    return None


@dataclass
class FilaEscala:
    categoria: str
    monto: Decimal
    vigencia_desde: date
    no_remunerativo: Decimal = Decimal("0")
    asignacion_unica: Decimal = Decimal("0")


@dataclass
class ResultadoImportacion:
    filas: list = field(default_factory=list)
    errores: list = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errores


def agregar_fila(ws, categoria: str, vigencia: date, monto=None, no_rem=None, asig_unica=None) -> None:
    def num(v):
        return float(v) if v is not None else None
    ws.append([categoria, num(monto), vigencia, num(no_rem), num(asig_unica)])
    for col in (2, 4, 5):
        ws.cell(row=ws.max_row, column=col).number_format = "#,##0.00"
    ws.cell(row=ws.max_row, column=3).number_format = "DD/MM/YYYY"


def generar_plantilla(ejemplo: dict | None = None, vigencia: date | None = None,
                      categorias: list | None = None) -> bytes:
    """Devuelve un .xlsx con la plantilla. `ejemplo` = {categoria: monto} opcional.

    `categorias`: las del convenio; por defecto las de Comercio.
    """
    wb = Workbook()
    ws = wb.active
    ws.title = HOJA
    ws.append(COLUMNAS)
    for celda in ws[1]:
        celda.font = Font(bold=True, color="FFFFFF")
        celda.fill = PatternFill("solid", fgColor="1F4E78")
    ws.column_dimensions["A"].width = 28
    ws.column_dimensions["B"].width = 16
    ws.column_dimensions["C"].width = 16
    ws.column_dimensions["D"].width = 18
    ws.column_dimensions["E"].width = 18
    vigencia = vigencia or date.today().replace(day=1)
    for cat in categorias or CATEGORIAS_COMERCIO:
        valor = (ejemplo or {}).get(cat)
        # `ejemplo` acepta {cat: monto} o {cat: (monto, no_remunerativo[, asig_unica])}
        montos = list(valor) if isinstance(valor, tuple) else [valor]
        montos += [None] * (3 - len(montos))
        agregar_fila(ws, cat, vigencia, *montos)

    ayuda = wb.create_sheet("Instrucciones")
    for linea in (
        "Completá la hoja 'Escala' con el básico mensual (jornada completa) de cada categoría.",
        "Monto: número sin símbolo $. Se aceptan decimales.",
        "Vigencia desde: fecha a partir de la cual rige el monto (DD/MM/AAAA).",
        "No remunerativo: suma no remunerativa del acuerdo para ese mes y categoría, jornada completa. Vacío = 0.",
        "Si el acuerdo cambia la suma no remunerativa mes a mes, cargá una fila por mes.",
        "Asig. única vez: asignación extraordinaria no remunerativa; se paga solo en el mes de esa vigencia. Vacío = 0.",
        "Para un acuerdo nuevo, agregá filas con la nueva vigencia; no borres las anteriores.",
        "Las categorías tienen que coincidir con las del convenio listadas en la plantilla.",
    ):
        ayuda.append([linea])
    ayuda.column_dimensions["A"].width = 100

    buf = BytesIO()
    wb.save(buf)
    return buf.getvalue()


def _parse_fecha(valor) -> date:
    if isinstance(valor, datetime):
        return valor.date()
    if isinstance(valor, date):
        return valor
    if isinstance(valor, str):
        texto = valor.strip()
        for fmt in ("%d/%m/%Y", "%Y-%m-%d", "%d-%m-%Y"):
            try:
                return datetime.strptime(texto, fmt).date()
            except ValueError:
                pass
    raise ValueError(f"fecha inválida: {valor!r}")


def _parse_monto(valor, permitir_cero: bool = False) -> Decimal:
    if isinstance(valor, (int, float)):
        monto = Decimal(str(valor))
    elif isinstance(valor, str):
        texto = valor.strip().replace("$", "").replace(" ", "")
        # Formato argentino 1.234.567,89
        if "," in texto:
            texto = texto.replace(".", "").replace(",", ".")
        try:
            monto = Decimal(texto)
        except InvalidOperation:
            raise ValueError(f"monto inválido: {valor!r}")
    else:
        raise ValueError(f"monto inválido: {valor!r}")
    if monto < 0 or (monto == 0 and not permitir_cero):
        raise ValueError(f"el monto tiene que ser mayor a cero: {valor!r}")
    return monto.quantize(Decimal("0.01"))


def leer_plantilla(contenido: bytes, categorias: list | None = None) -> ResultadoImportacion:
    """Lee y valida la plantilla. No toca la base.

    Sin `categorias` valida contra Comercio (acepta los nombres publicados, ver
    `normalizar_categoria`); con `categorias` busca el nombre exacto, sin
    distinguir mayúsculas.
    """
    por_nombre = {" ".join(c.lower().split()): c for c in categorias} if categorias else None
    res = ResultadoImportacion()
    try:
        wb = load_workbook(BytesIO(contenido), data_only=True)
    except Exception as exc:  # archivo corrupto o no es xlsx
        res.errores.append(f"No se pudo abrir el Excel: {exc}")
        return res
    if HOJA not in wb.sheetnames:
        res.errores.append(f"Falta la hoja '{HOJA}'")
        return res
    ws = wb[HOJA]
    encabezado = tuple((c.value or "").strip() if isinstance(c.value, str) else c.value
                       for c in ws[1][:len(COLUMNAS)])
    if encabezado[:3] != COLUMNAS_OBLIGATORIAS or any(
            h not in (None, COLUMNAS[i]) for i, h in enumerate(encabezado) if i >= 3):
        res.errores.append(f"Encabezado esperado {COLUMNAS}, se encontró {encabezado}")
        return res

    vistos = set()
    for n, fila in enumerate(ws.iter_rows(min_row=2, max_col=len(COLUMNAS), values_only=True), start=2):
        fila = tuple(fila) + (None,) * (len(COLUMNAS) - len(fila))
        if all(v is None or (isinstance(v, str) and not v.strip()) for v in fila):
            continue
        cat_raw, monto_raw, vig_raw, no_rem_raw, asig_raw = fila
        errores_fila = []
        if por_nombre is None:
            cat = normalizar_categoria(cat_raw)
        else:
            cat = por_nombre.get(" ".join(str(cat_raw or "").lower().split()))
        if cat is None:
            errores_fila.append(f"categoría desconocida {cat_raw!r}")
        try:
            monto = _parse_monto(monto_raw)
        except ValueError as exc:
            errores_fila.append(str(exc))
        try:
            vigencia = _parse_fecha(vig_raw)
        except ValueError as exc:
            errores_fila.append(str(exc))
        opcionales = []
        for nombre, raw in (("no remunerativo", no_rem_raw), ("asig. única vez", asig_raw)):
            valor = Decimal("0")
            if raw is not None and not (isinstance(raw, str) and not raw.strip()):
                try:
                    valor = _parse_monto(raw, permitir_cero=True)
                except ValueError as exc:
                    errores_fila.append(f"{nombre}: {exc}")
            opcionales.append(valor)
        if not errores_fila:
            clave = (cat, vigencia)
            if clave in vistos:
                errores_fila.append(f"{cat} con vigencia {vigencia:%d/%m/%Y} está repetida")
            vistos.add(clave)
        if errores_fila:
            res.errores.append(f"Fila {n}: " + "; ".join(errores_fila))
        else:
            res.filas.append(FilaEscala(cat, monto, vigencia, *opcionales))
    if not res.filas and not res.errores:
        res.errores.append("La plantilla no tiene filas con datos")
    return res


def guardar_escala(conn: sqlite3.Connection, filas: list, convenio: str = CONVENIO_COMERCIO,
                   fuente: str | None = None) -> int:
    """Inserta o actualiza las filas (misma categoría + vigencia pisa el monto)."""
    for f in filas:
        conn.execute(
            """INSERT INTO escalas (convenio, categoria, monto, vigencia_desde, no_remunerativo,
                                   asignacion_unica, fuente, verificada)
               VALUES (?, ?, ?, ?, ?, ?, ?, 0)
               ON CONFLICT (convenio, categoria, vigencia_desde)
               DO UPDATE SET monto = excluded.monto, no_remunerativo = excluded.no_remunerativo,
                             asignacion_unica = excluded.asignacion_unica,
                             fuente = excluded.fuente, verificada = 0""",
            (convenio, f.categoria, str(f.monto), f.vigencia_desde.isoformat(), str(f.no_remunerativo),
             str(f.asignacion_unica), fuente),
        )
    conn.commit()
    return len(filas)


def basico_vigente(conn: sqlite3.Connection, categoria: str, al: date,
                   convenio: str = CONVENIO_COMERCIO) -> FilaEscala | None:
    """Básico de la categoría con la vigencia más reciente que no sea posterior a `al`."""
    row = conn.execute(
        """SELECT categoria, monto, vigencia_desde, no_remunerativo, asignacion_unica FROM escalas
           WHERE convenio = ? AND categoria = ? AND vigencia_desde <= ?
           ORDER BY vigencia_desde DESC LIMIT 1""",
        (convenio, categoria, al.isoformat()),
    ).fetchone()
    if row is None:
        return None
    return FilaEscala(row["categoria"], Decimal(row["monto"]), date.fromisoformat(row["vigencia_desde"]),
                      Decimal(row["no_remunerativo"]), Decimal(row["asignacion_unica"]))
