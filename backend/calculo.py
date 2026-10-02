"""Motor de cálculo para empleados de comercio (CCT 130/75).

Alcance de esta versión:
- Básico mensual de jornada completa según escala vigente.
- Antigüedad: 1% del básico por año cumplido (art. 24 CCT 130/75).
- Presentismo: 8,33% sobre básico + antigüedad (art. 40 CCT 130/75). Se pierde
  con cualquier inasistencia injustificada.
- Descuento de días por inasistencias injustificadas: (básico + antigüedad) / 30 por día.
- Aportes del trabajador: jubilación 11%, Ley 19.032 3%, obra social 3%,
  FAECYS 0,5% (art. 100 CCT) y cuota sindical 2% solo para afiliados.

Fuera de alcance (todavía): sumas no remunerativas de acuerdos paritarios,
jornada parcial, horas extra, SAC, vacaciones, licencias y ganancias.
"""
from calendar import monthrange
from dataclasses import dataclass, field, asdict
from datetime import date
from decimal import Decimal, ROUND_HALF_UP

from .formato import pesos

CENTAVO = Decimal("0.01")

PORC_ANTIGUEDAD_ANUAL = Decimal("0.01")
PORC_PRESENTISMO = Decimal("0.0833")
DIAS_MES = Decimal("30")

# (código, descripción, porcentaje, aplica_tope, solo_afiliados)
APORTES = [
    ("JUB", "Jubilación SIPA (Ley 24.241)", Decimal("0.11"), True, False),
    ("PAMI", "Ley 19.032 (INSSJP)", Decimal("0.03"), True, False),
    ("OS", "Obra Social (Ley 23.660)", Decimal("0.03"), True, False),
    ("FAECYS", "Aporte FAECYS art. 100 CCT 130/75", Decimal("0.005"), False, False),
    ("SIND", "Cuota sindical", Decimal("0.02"), False, True),
]


def redondear(valor: Decimal) -> Decimal:
    return valor.quantize(CENTAVO, rounding=ROUND_HALF_UP)


def ultimo_dia(periodo: str) -> date:
    anio, mes = (int(p) for p in periodo.split("-"))
    return date(anio, mes, monthrange(anio, mes)[1])


def primer_dia(periodo: str) -> date:
    anio, mes = (int(p) for p in periodo.split("-"))
    return date(anio, mes, 1)


def anios_cumplidos(desde: date, hasta: date) -> int:
    if hasta < desde:
        return 0
    anios = hasta.year - desde.year
    if (hasta.month, hasta.day) < (desde.month, desde.day):
        anios -= 1
    return anios


def _pct(valor: Decimal) -> str:
    texto = f"{valor * 100:.2f}".rstrip("0").rstrip(".")
    return texto.replace(".", ",") + "%"


@dataclass
class Concepto:
    codigo: str
    descripcion: str
    detalle: str          # cómo se determinó (art. 140 inc. c LCT)
    tipo: str             # "remunerativo" | "descuento"
    importe: Decimal


@dataclass
class Liquidacion:
    periodo: str
    categoria: str
    basico_escala: Decimal
    vigencia_escala: date
    anios_antiguedad: int
    dias_trabajados: int
    conceptos: list = field(default_factory=list)
    total_remunerativo: Decimal = Decimal("0")
    total_descuentos: Decimal = Decimal("0")
    neto: Decimal = Decimal("0")

    def remunerativos(self):
        return [c for c in self.conceptos if c.tipo == "remunerativo"]

    def descuentos(self):
        return [c for c in self.conceptos if c.tipo == "descuento"]

    def to_dict(self) -> dict:
        def conv(v):
            if isinstance(v, Decimal):
                return str(v)
            if isinstance(v, date):
                return v.isoformat()
            if isinstance(v, list):
                return [conv(x) for x in v]
            if isinstance(v, dict):
                return {k: conv(x) for k, x in v.items()}
            return v
        return conv(asdict(self))

    @classmethod
    def from_dict(cls, d: dict) -> "Liquidacion":
        conceptos = [Concepto(**{**c, "importe": Decimal(c["importe"])}) for c in d["conceptos"]]
        return cls(
            periodo=d["periodo"], categoria=d["categoria"],
            basico_escala=Decimal(d["basico_escala"]),
            vigencia_escala=date.fromisoformat(d["vigencia_escala"]),
            anios_antiguedad=d["anios_antiguedad"], dias_trabajados=d["dias_trabajados"],
            conceptos=conceptos,
            total_remunerativo=Decimal(d["total_remunerativo"]),
            total_descuentos=Decimal(d["total_descuentos"]),
            neto=Decimal(d["neto"]),
        )


def liquidar_comercio(
    *,
    periodo: str,
    categoria: str,
    basico: Decimal,
    vigencia_escala: date,
    fecha_ingreso: date,
    afiliado_sindicato: bool = False,
    inasistencias_injustificadas: int = 0,
    tope_base_imponible: Decimal | None = None,
) -> Liquidacion:
    if inasistencias_injustificadas < 0 or inasistencias_injustificadas > 30:
        raise ValueError("inasistencias_injustificadas tiene que estar entre 0 y 30")
    fin = ultimo_dia(periodo)
    if fecha_ingreso > fin:
        raise ValueError("el empleado ingresó después del período liquidado")

    anios = anios_cumplidos(fecha_ingreso, fin)
    liq = Liquidacion(
        periodo=periodo, categoria=categoria, basico_escala=basico,
        vigencia_escala=vigencia_escala, anios_antiguedad=anios,
        dias_trabajados=30 - inasistencias_injustificadas,
    )

    basico = redondear(basico)
    liq.conceptos.append(Concepto(
        "BAS", "Sueldo básico", f"{categoria} · 30 días · mensual", "remunerativo", basico))

    antiguedad = redondear(basico * PORC_ANTIGUEDAD_ANUAL * anios)
    if antiguedad:
        liq.conceptos.append(Concepto(
            "ANT", "Antigüedad", f"{anios} años × 1% s/ básico", "remunerativo", antiguedad))

    if inasistencias_injustificadas:
        descuento = redondear((basico + antiguedad) / DIAS_MES * inasistencias_injustificadas)
        liq.conceptos.append(Concepto(
            "INAS", "Inasistencias injustificadas",
            f"{inasistencias_injustificadas} días × (básico + antig.) / 30",
            "remunerativo", -descuento))
    else:
        presentismo = redondear((basico + antiguedad) * PORC_PRESENTISMO)
        liq.conceptos.append(Concepto(
            "PRES", "Presentismo", f"{_pct(PORC_PRESENTISMO)} s/ básico + antig.",
            "remunerativo", presentismo))

    total_rem = sum((c.importe for c in liq.remunerativos()), Decimal("0"))
    liq.total_remunerativo = total_rem

    for codigo, desc, pct, aplica_tope, solo_afiliados in APORTES:
        if solo_afiliados and not afiliado_sindicato:
            continue
        base = total_rem
        detalle = f"{_pct(pct)} s/ $ {pesos(base)}"
        if aplica_tope and tope_base_imponible is not None and base > tope_base_imponible:
            base = tope_base_imponible
            detalle = f"{_pct(pct)} s/ tope $ {pesos(base)}"
        liq.conceptos.append(Concepto(codigo, desc, detalle, "descuento", redondear(base * pct)))

    liq.total_descuentos = sum((c.importe for c in liq.descuentos()), Decimal("0"))
    liq.neto = liq.total_remunerativo - liq.total_descuentos
    return liq
