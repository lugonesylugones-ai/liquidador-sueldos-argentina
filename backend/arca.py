"""Archivos para el Libro de Sueldos Digital de ARCA, que arma la DJ F.931.

Dos archivos de texto (ANSI, campos de largo fijo), según los diseños publicados por ARCA en
https://www.arca.gob.ar/LibrodeSueldosDigital (ayuda > Diseños):

- Conceptos ("Diseño de interfaz - conceptos"): relaciona cada concepto del liquidador con un
  concepto ARCA y dice a qué bases de cálculo suma. Se sube una sola vez por empleador, en
  "Parametrización de conceptos", y otra vez si aparece un concepto nuevo.
- Liquidación ("Diseño de interfaz - liquidación"): un registro '01' del envío y, por trabajador,
  un '02' (datos del pago), un '03' por concepto del recibo y un '04' (datos para el F.931 con
  las nueve bases imponibles más la 10). Se sube en "Liquidaciones > Importar desde archivo".
  Va una liquidación por recibo (el sueldo es la 1; la zona fría aparte, la 2...): ARCA suma
  las bases de todas para el F.931, como en los TXT reales de los consorcios de sep-2026.
"""
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

# Subsistemas del diseño de conceptos, en el orden de sus columnas (posiciones 168 a 186).
# Cada concepto dice con 0/1 si suma a la base de ese subsistema.
_COLUMNAS = ("sipa_ap", "sipa_co", "inssjp_ap", "inssjp_co", "os_ap", "os_co", "fsr_ap", "fsr_co",
             "renatea_ap", "renatea_co", None, "aaff_co", None, "fne_co", None, "lrt_co",
             "dif_ap", None, "esp_ap")
# Base imponible (Rem 1 a 9 del F.931) que forma cada subsistema ("Detalle de conceptos").
_BASE_DE = {"sipa_ap": 1, "sipa_co": 2, "inssjp_ap": 5, "inssjp_co": 2, "os_ap": 4, "os_co": 8,
            "fsr_ap": 4, "fsr_co": 8, "renatea_ap": 1, "renatea_co": 3, "aaff_co": 3, "fne_co": 3,
            "lrt_co": 9, "dif_ap": 6, "esp_ap": 7}

# Perfiles de bases. Remunerativo: todos los subsistemas del régimen general.
REM = frozenset({"sipa_ap", "sipa_co", "inssjp_ap", "inssjp_co", "os_ap", "os_co", "fsr_ap", "fsr_co",
                 "aaff_co", "fne_co", "lrt_co"})
# Acuerdos no remunerativos de Comercio: obra social (aportes y contribuciones) y ART, como los
# declara el F.931 de septiembre 2026 (Rem 4 y 8 con el no remunerativo; Rem 9 también).
NR_OS = frozenset({"os_ap", "os_co", "fsr_ap", "fsr_co", "lrt_co"})
SIN_BASE = frozenset()


@dataclass(frozen=True)
class ConceptoArca:
    arca: str            # código de concepto ARCA (6)
    descripcion: str
    bases: frozenset
    unidades: str = ""   # 'H' horas, 'D' días: el '03' informa la cantidad


# Concepto del liquidador -> concepto ARCA. Los descuentos no llevan bases.
CONCEPTOS = {
    # Remunerativos
    "BAS": ConceptoArca("110000", "Sueldo básico", REM),
    "INAS": ConceptoArca("110000", "Inasistencias injustificadas", REM, "D"),
    "VIV": ConceptoArca("110004", "Valor vivienda", REM),
    "VIVE": ConceptoArca("110004", "Vivienda (en especie)", REM),
    "SAC": ConceptoArca("120000", "Sueldo anual complementario", REM),
    "SACP": ConceptoArca("120003", "SAC proporcional", REM, "D"),
    "HE50": ConceptoArca("130001", "Horas extra 50%", REM, "H"),
    "HE100": ConceptoArca("130002", "Horas extra 100%", REM, "H"),
    "ZONA": ConceptoArca("140000", "Zona fría / desfavorable", REM),
    "ADR": ConceptoArca("160000", "Suma fija remunerativa CCT", REM),
    "ANT": ConceptoArca("160001", "Antigüedad", REM),
    "TIT": ConceptoArca("160002", "Título", REM),
    "RES": ConceptoArca("160003", "Retiro de residuos", REM),
    "CLAS": ConceptoArca("160003", "Plus clasificación de residuos", REM),
    "JARD": ConceptoArca("160003", "Plus jardín", REM),
    "COCH": ConceptoArca("160003", "Plus limpieza cocheras", REM),
    "MOVC": ConceptoArca("160003", "Plus movimiento de coches", REM),
    "PILE": ConceptoArca("160003", "Plus limpieza piletas", REM),
    "PRES": ConceptoArca("170001", "Asistencia y puntualidad", REM),
    "VIAT": ConceptoArca("170005", "Adicional viáticos", REM),
    # No remunerativos
    "NR": ConceptoArca("540000", "Acuerdo no remunerativo", NR_OS),
    "ANTNR": ConceptoArca("540000", "Antigüedad no remunerativa", NR_OS),
    "PRESNR": ConceptoArca("540000", "Asistencia y puntualidad no remunerativa", NR_OS),
    "INASNR": ConceptoArca("540000", "Inasistencias s/ no remunerativo", NR_OS, "D"),
    "SACNR": ConceptoArca("540000", "SAC s/ no remunerativo", NR_OS),
    "EXTR": ConceptoArca("540000", "Asignación extraordinaria", NR_OS),
    "RED": ConceptoArca("799999", "Redondeo", SIN_BASE),
    # Liquidación final: sin aportes (art. 7 Ley 24.241)
    "VAC": ConceptoArca("520012", "Vacaciones no gozadas", SIN_BASE),
    "VACNR": ConceptoArca("520012", "Vacaciones no gozadas s/ no remunerativo", SIN_BASE),
    "SACVAC": ConceptoArca("520018", "SAC s/ vacaciones no gozadas", SIN_BASE),
    "IND": ConceptoArca("520014", "Indemnización por antigüedad", SIN_BASE),
    "PREAV": ConceptoArca("520015", "Indemnización sustitutiva de preaviso", SIN_BASE),
    "SACPREAV": ConceptoArca("520017", "SAC s/ preaviso", SIN_BASE),
    "INTEG": ConceptoArca("520016", "Integración mes de despido", SIN_BASE),
    "SACINTEG": ConceptoArca("520017", "SAC s/ integración mes de despido", SIN_BASE),
    # Descuentos
    "JUB": ConceptoArca("810000", "Jubilación 11%", SIN_BASE),
    "PAMI": ConceptoArca("810001", "Ley 19.032 INSSJP 3%", SIN_BASE),
    "OS": ConceptoArca("810002", "Obra social 3%", SIN_BASE),
    "SIND": ConceptoArca("810004", "Cuota sindical", SIN_BASE),
    "A27B": ConceptoArca("810005", "Seguro de vida art. 27 bis CCT 589/10", SIN_BASE),
    "A100": ConceptoArca("820000", "Art. 100 CCT 130/75", SIN_BASE),
    "A101": ConceptoArca("820000", "Art. 101 CCT 130/75", SIN_BASE),
    "A101C": ConceptoArca("820000", "Compl. Art. 101 CCT 130/75", SIN_BASE),
    "FAECYS": ConceptoArca("820000", "FAECYS CCT 130/75", SIN_BASE),
    "CPF": ConceptoArca("820000", "Caja Protección Familia art. 19 CCT 589/10", SIN_BASE),
    "FMVDD": ConceptoArca("820000", "FMVDD art. 27 CCT 589/10", SIN_BASE),
    "DMUT": ConceptoArca("820000", "Mutual", SIN_BASE),
    "DEMB": ConceptoArca("820000", "Embargo judicial", SIN_BASE),
    "DPRE": ConceptoArca("810007", "Cuota préstamo", SIN_BASE),
    "DANT": ConceptoArca("820000", "Anticipo de haberes", SIN_BASE),
    "DOTR": ConceptoArca("820000", "Otro descuento", SIN_BASE),
}

# Detracción de la base de contribuciones (art. 22 Ley 27.541) por trabajador de jornada completa;
# en jornada parcial, proporcional a las horas. El F.931 de 09/2026 la aplica así (3 × 5.836,40).
DETRACCION_LEY_27541 = Decimal("7003.68")

# Valores por defecto del registro '04' (tablas de Declaración en Línea).
TIPO_EMPLEADOR = "1"        # Dec. 814/01 art. 2 inc. b) (contribuciones al 18%)
ACTIVIDAD = "049"
ZONA = "04"                 # Resto de Buenos Aires (Bahía Blanca)
CONDICION = "01"            # Servicios comunes, mayor de 18 años
SITUACION_ACTIVO = "01"
MODALIDAD_COMPLETA = "008"  # Tiempo completo indeterminado
MODALIDAD_PARCIAL = "001"   # Tiempo parcial indeterminado
OBRA_SOCIAL = {"CCT 130/75": "126205", "CCT 589/10": "106401"}   # OSECAC / OSPERYH


class ErrorArca(ValueError):
    pass


@dataclass
class Trabajador:
    """Todo lo que el '02', '03' y '04' necesitan de un trabajador en el período."""
    cuil: str
    legajo: str
    jornada_horas: int
    obra_social: str
    fecha_pago: date
    conceptos: list                     # Concepto del liquidador (código, tipo, importe), ya sumados
    dias_trabajados: int = 30
    factor_obra_social: Decimal = Decimal("1")   # jornada parcial con OS sobre jornada completa
    conyuge: bool = False
    hijos: int = 0
    cbu: str = ""
    cantidades: dict = field(default_factory=dict)   # código -> cantidad (horas, días)
    propios: dict = field(default_factory=dict)      # conceptos del usuario: código -> ConceptoArca
    # La detracción y los días van una sola vez por mes: en la primera liquidación del trabajador
    # (el sueldo). En las otras del mismo período (zona fría aparte, aguinaldo) van en cero.
    principal: bool = True


def _alfa(valor: str, largo: int) -> str:
    texto = (valor or "").strip()
    if len(texto) > largo:
        texto = texto[:largo]
    return texto.ljust(largo)


def _num(valor: int, largo: int) -> str:
    texto = str(int(valor))
    if len(texto) > largo or int(valor) < 0:
        raise ErrorArca(f"El número {valor} no entra en {largo} posiciones")
    return texto.zfill(largo)


def _importe(valor: Decimal, largo: int = 15) -> str:
    """13 enteros y 2 decimales, sin coma ni punto."""
    return _num(int((abs(valor) * 100).quantize(Decimal("1"))), largo)


def _cuit(valor: str) -> str:
    digitos = "".join(c for c in valor if c.isdigit())
    if len(digitos) != 11:
        raise ErrorArca(f"CUIT/CUIL inválido: {valor}")
    return digitos


def concepto_arca(codigo: str, propios: dict | None = None) -> ConceptoArca:
    """`propios`: los conceptos que cargó el usuario, que no pisan a los del liquidador."""
    try:
        return CONCEPTOS[codigo] if codigo in CONCEPTOS else (propios or {})[codigo]
    except KeyError:
        raise ErrorArca(f"El concepto {codigo} no tiene concepto ARCA asignado") from None


def bases_propio(tipo: str, aportes: bool) -> frozenset:
    """Bases de un concepto del usuario: remunerativo, todas; no remunerativo con obra social, las
    de los acuerdos de Comercio; el resto, ninguna."""
    if tipo == "remunerativo":
        return REM
    return NR_OS if tipo == "no_remunerativo" and aportes else SIN_BASE


def archivo_conceptos(codigos, propios: dict | None = None) -> str:
    """Relación conceptos del empleador - ARCA, 195 posiciones por línea."""
    lineas = []
    for codigo in sorted(set(codigos)):
        c = concepto_arca(codigo, propios)
        marcas = "".join("0" if col is None else ("1" if col in c.bases else "0") for col in _COLUMNAS)
        linea = c.arca + _alfa(codigo, 10) + _alfa(c.descripcion, 150) + "0" + marcas + " " * 9
        assert len(linea) == 195, len(linea)
        lineas.append(linea)
    return "\r\n".join(lineas) + "\r\n"


def bases(t: Trabajador) -> dict:
    """Las bases imponibles 1 a 10, la remuneración bruta y la detracción del trabajador."""
    b = {n: Decimal("0") for n in range(1, 10)}
    bruta = Decimal("0")
    for codigo, tipo, importe in t.conceptos:
        if tipo == "descuento":
            continue
        bruta += importe
        for n in {_BASE_DE[s] for s in concepto_arca(codigo, t.propios).bases}:
            b[n] += importe
    for n in (4, 8):
        b[n] = (b[n] * t.factor_obra_social).quantize(Decimal("0.01"))
    detraccion = Decimal("0")
    if t.principal:
        detraccion = (DETRACCION_LEY_27541 * min(t.jornada_horas, 8) / 8).quantize(Decimal("0.01"))
        detraccion = min(detraccion, b[3])
    b[10] = b[3] - detraccion
    return {"bases": b, "bruta": bruta, "detraccion": detraccion}


def _registro_02(t: Trabajador) -> str:
    forma_pago = "3" if t.cbu else "1"
    linea = ("02" + _cuit(t.cuil) + _alfa(t.legajo, 10) + _alfa("", 50) + _alfa(t.cbu, 22)
             + _num(0, 3) + t.fecha_pago.strftime("%Y%m%d") + " " * 8 + forma_pago)
    assert len(linea) == 115, len(linea)
    return linea


def _registros_03(t: Trabajador) -> list:
    lineas = []
    for codigo, tipo, importe in t.conceptos:
        if not importe:
            continue
        c = concepto_arca(codigo, t.propios)
        credito = tipo != "descuento" and importe > 0
        cantidad = t.cantidades.get(codigo, Decimal("0"))
        linea = ("03" + _cuit(t.cuil) + _alfa(codigo, 10) + _num(int(Decimal(cantidad) * 100), 5)
                 + _alfa(c.unidades if cantidad else "", 1) + _importe(importe)
                 + ("C" if credito else "D") + " " * 6)
        assert len(linea) == 51, len(linea)
        lineas.append(linea)
    return lineas


def _registro_04(t: Trabajador, *, tipo_empleador: str, actividad: str, zona: str) -> str:
    d = bases(t)
    b = d["bases"]
    modalidad = MODALIDAD_COMPLETA if t.jornada_horas >= 8 else MODALIDAD_PARCIAL
    linea = ("04" + _cuit(t.cuil) + ("1" if t.conyuge else "0") + _num(t.hijos, 2)
             + "1"            # trabajador en convenio colectivo
             + "1"            # cubierto por el seguro colectivo de vida obligatorio
             + "0"            # no corresponde reducción
             + _alfa(tipo_empleador, 1) + "0" + SITUACION_ACTIVO + CONDICION + _alfa(actividad, 3)
             + modalidad + "00" + _alfa(zona, 2)
             + SITUACION_ACTIVO + "01" + "00" + "00" + "00" + "00"       # situación de revista 1 a 3
             + _num(t.dias_trabajados if t.principal else 0, 2) + _num(0, 3)
             + _importe(Decimal("0"), 5) + _importe(Decimal("0"), 5)      # % aporte adicional / tarea dif.
             + _alfa(t.obra_social, 6) + _num(0, 2)
             + _importe(Decimal("0")) * 5                                  # adicionales OS y bases dif.
             + _importe(Decimal("0"))                                      # remuneración maternidad
             + _importe(d["bruta"])
             + "".join(_importe(b[n]) for n in range(1, 10))
             + _importe(Decimal("0")) * 2                                  # bases dif. de seg. social
             + _importe(b[10]) + _importe(d["detraccion"]))
    assert len(linea) == 370, len(linea)
    return linea


def archivo_liquidacion(*, cuit_empleador: str, periodo: str, trabajadores: list, numero: int = 1,
                        tipo_empleador: str = TIPO_EMPLEADOR, actividad: str = ACTIVIDAD,
                        zona: str = ZONA) -> str:
    """Liquidación de sueldos del período con los datos de la DJ F.931 (envío 'SJ')."""
    if not trabajadores:
        raise ErrorArca("No hay liquidaciones en el período")
    lineas = ["01" + _cuit(cuit_empleador) + "SJ" + periodo.replace("-", "") + "M" + _num(numero, 5)
              + "30" + _num(len(trabajadores), 6)]
    for t in trabajadores:
        lineas.append(_registro_02(t))
        lineas.extend(_registros_03(t))
        lineas.append(_registro_04(t, tipo_empleador=tipo_empleador, actividad=actividad, zona=zona))
    return "\r\n".join(lineas) + "\r\n"
