"""Carga de escalas salariales desde la plantilla Excel propia.

La plantilla tiene una hoja "Escala" con estas columnas:
    Categoría | Monto | Vigencia desde | No remunerativo

Cada fila es el básico mensual de jornada completa de una categoría a partir
de una fecha, y la suma no remunerativa del acuerdo para ese mes (opcional,
vacía = 0). Así se cargan las circulares de FAECYS, que traen por categoría y
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
COLUMNAS = ("Categoría", "Monto", "Vigencia desde", "No remunerativo")
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


@dataclass
class FilaEscala:
    categoria: str
    monto: Decimal
    vigencia_desde: date
    no_remunerativo: Decimal = Decimal("0")


@dataclass
class ResultadoImportacion:
    filas: list = field(default_factory=list)
    errores: list = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errores


def generar_plantilla(ejemplo: dict | None = None, vigencia: date | None = None) -> bytes:
    """Devuelve un .xlsx con la plantilla. `ejemplo` = {categoria: monto} opcional."""
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
    vigencia = vigencia or date.today().replace(day=1)
    for cat in CATEGORIAS_COMERCIO:
        valor = (ejemplo or {}).get(cat)
        # `ejemplo` acepta {cat: monto} o {cat: (monto, no_remunerativo)}
        monto, no_rem = valor if isinstance(valor, tuple) else (valor, None)
        ws.append([cat, float(monto) if monto is not None else None, vigencia,
                   float(no_rem) if no_rem is not None else None])
        ws.cell(row=ws.max_row, column=2).number_format = "#,##0.00"
        ws.cell(row=ws.max_row, column=3).number_format = "DD/MM/YYYY"
        ws.cell(row=ws.max_row, column=4).number_format = "#,##0.00"

    ayuda = wb.create_sheet("Instrucciones")
    for linea in (
        "Completá la hoja 'Escala' con el básico mensual (jornada completa) de cada categoría.",
        "Monto: número sin símbolo $. Se aceptan decimales.",
        "Vigencia desde: fecha a partir de la cual rige el monto (DD/MM/AAAA).",
        "No remunerativo: suma no remunerativa del acuerdo para ese mes y categoría, jornada completa. Vacío = 0.",
        "Si el acuerdo cambia la suma no remunerativa mes a mes, cargá una fila por mes.",
        "Para un acuerdo nuevo, agregá filas con la nueva vigencia; no borres las anteriores.",
        "Las categorías tienen que coincidir con las del CCT 130/75 listadas en la plantilla.",
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


def leer_plantilla(contenido: bytes) -> ResultadoImportacion:
    """Lee y valida la plantilla. No toca la base."""
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
    encabezado = tuple((c.value or "").strip() if isinstance(c.value, str) else c.value for c in ws[1][:4])
    if encabezado[:3] != COLUMNAS_OBLIGATORIAS or (len(encabezado) == 4 and encabezado[3] not in (None, COLUMNAS[3])):
        res.errores.append(f"Encabezado esperado {COLUMNAS}, se encontró {encabezado}")
        return res

    categorias_validas = {c.lower(): c for c in CATEGORIAS_COMERCIO}
    vistos = set()
    for n, fila in enumerate(ws.iter_rows(min_row=2, max_col=4, values_only=True), start=2):
        fila = tuple(fila) + (None,) * (4 - len(fila))
        if all(v is None or (isinstance(v, str) and not v.strip()) for v in fila):
            continue
        cat_raw, monto_raw, vig_raw, no_rem_raw = fila
        errores_fila = []
        cat = categorias_validas.get(str(cat_raw or "").strip().lower())
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
        no_rem = Decimal("0")
        if no_rem_raw is not None and not (isinstance(no_rem_raw, str) and not no_rem_raw.strip()):
            try:
                no_rem = _parse_monto(no_rem_raw, permitir_cero=True)
            except ValueError as exc:
                errores_fila.append(f"no remunerativo: {exc}")
        if not errores_fila:
            clave = (cat, vigencia)
            if clave in vistos:
                errores_fila.append(f"{cat} con vigencia {vigencia:%d/%m/%Y} está repetida")
            vistos.add(clave)
        if errores_fila:
            res.errores.append(f"Fila {n}: " + "; ".join(errores_fila))
        else:
            res.filas.append(FilaEscala(cat, monto, vigencia, no_rem))
    if not res.filas and not res.errores:
        res.errores.append("La plantilla no tiene filas con datos")
    return res


def guardar_escala(conn: sqlite3.Connection, filas: list, convenio: str = CONVENIO_COMERCIO) -> int:
    """Inserta o actualiza las filas (misma categoría + vigencia pisa el monto)."""
    for f in filas:
        conn.execute(
            """INSERT INTO escalas (convenio, categoria, monto, vigencia_desde, no_remunerativo)
               VALUES (?, ?, ?, ?, ?)
               ON CONFLICT (convenio, categoria, vigencia_desde)
               DO UPDATE SET monto = excluded.monto, no_remunerativo = excluded.no_remunerativo""",
            (convenio, f.categoria, str(f.monto), f.vigencia_desde.isoformat(), str(f.no_remunerativo)),
        )
    conn.commit()
    return len(filas)


def basico_vigente(conn: sqlite3.Connection, categoria: str, al: date,
                   convenio: str = CONVENIO_COMERCIO) -> FilaEscala | None:
    """Básico de la categoría con la vigencia más reciente que no sea posterior a `al`."""
    row = conn.execute(
        """SELECT categoria, monto, vigencia_desde, no_remunerativo FROM escalas
           WHERE convenio = ? AND categoria = ? AND vigencia_desde <= ?
           ORDER BY vigencia_desde DESC LIMIT 1""",
        (convenio, categoria, al.isoformat()),
    ).fetchone()
    if row is None:
        return None
    return FilaEscala(row["categoria"], Decimal(row["monto"]), date.fromisoformat(row["vigencia_desde"]),
                      Decimal(row["no_remunerativo"]))
