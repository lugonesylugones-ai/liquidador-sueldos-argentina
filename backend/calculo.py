"""Motor de cálculo para empleados de comercio (CCT 130/75).

Alcance de esta versión:
- Básico mensual según escala vigente, proporcional a la jornada (horas diarias / 8).
- Antigüedad: 1% por año cumplido, sobre básico y sobre la suma no remunerativa
  (art. 24 CCT y circulares FAECYS).
- Presentismo: 8,33% sobre (básico + antigüedad) y sobre (no rem + antig. no rem)
  (art. 40 CCT). Se pierde con cualquier inasistencia injustificada.
- Inasistencias injustificadas: se descuenta 1/30 por día de cada bloque.
- Asignación extraordinaria no remunerativa: proporcional a la jornada, sin
  antigüedad ni presentismo.
- Aportes del trabajador: jubilación 11% y Ley 19.032 3% sobre lo remunerativo;
  obra social 3%, Art. 100 2%, Art. 101 2% y FAECYS 0,5% sobre remunerativo +
  no remunerativo. En jornada parcial la obra social y el Art. 101 se calculan
  sobre el equivalente a jornada completa.
- Neto redondeado para arriba al peso entero; la diferencia va como "Redondeo".

Fuera de alcance (todavía): horas extra, SAC, vacaciones, licencias y ganancias.
"""
from calendar import monthrange
from dataclasses import dataclass, field, asdict
from datetime import date
from decimal import Decimal, ROUND_CEILING, ROUND_HALF_UP

from .formato import pesos

CENTAVO = Decimal("0.01")

PORC_ANTIGUEDAD_ANUAL = Decimal("0.01")
PORC_PRESENTISMO = Decimal("0.0833")
DIAS_MES = Decimal("30")
HORAS_JORNADA_COMPLETA = 8

# Aportes sobre lo remunerativo (con tope opcional de base imponible).
APORTES_REMUNERATIVOS = [
    ("JUB", "Jubilación 11% (Ley 24.241)", Decimal("0.11")),
    ("PAMI", "Ley 19.032 INSSJP 3%", Decimal("0.03")),
]
# Aportes sobre remunerativo + no remunerativo:
# (código, descripción, porcentaje, se completa a jornada completa en jornada parcial)
APORTES_TOTALES = [
    ("OS", "Obra social 3%", Decimal("0.03"), True),
    ("A101", "Art. 101 CCT 130/75", Decimal("0.02"), False),
    ("A100", "Art. 100 CCT 130/75", Decimal("0.02"), False),
    ("FAECYS", "FAECYS CCT 130/75", Decimal("0.005"), False),
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
    tipo: str             # "remunerativo" | "no_remunerativo" | "descuento"
    importe: Decimal


@dataclass
class Liquidacion:
    periodo: str
    categoria: str
    basico_escala: Decimal
    no_remunerativo_escala: Decimal
    vigencia_escala: date
    jornada_horas: int
    anios_antiguedad: int
    dias_trabajados: int
    conceptos: list = field(default_factory=list)
    total_remunerativo: Decimal = Decimal("0")
    total_no_remunerativo: Decimal = Decimal("0")
    total_descuentos: Decimal = Decimal("0")
    neto: Decimal = Decimal("0")

    def de_tipo(self, tipo: str):
        return [c for c in self.conceptos if c.tipo == tipo]

    def remunerativos(self):
        return self.de_tipo("remunerativo")

    def no_remunerativos(self):
        return self.de_tipo("no_remunerativo")

    def descuentos(self):
        return self.de_tipo("descuento")

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
        decimales = ("basico_escala", "no_remunerativo_escala", "total_remunerativo",
                     "total_no_remunerativo", "total_descuentos", "neto")
        datos = {k: (Decimal(v) if k in decimales else v) for k, v in d.items()}
        datos["vigencia_escala"] = date.fromisoformat(d["vigencia_escala"])
        datos["conceptos"] = [Concepto(**{**c, "importe": Decimal(c["importe"])}) for c in d["conceptos"]]
        return cls(**datos)


def _bloque(liq: Liquidacion, *, tipo: str, sufijo: str, monto: Decimal, desc_monto: str,
            detalle_monto: str, anios: int, inasistencias: int) -> None:
    """Agrega monto + antigüedad + (presentismo o descuento por faltas) de un bloque."""
    cod = "" if tipo == "remunerativo" else "NR"
    if not monto:
        return
    liq.conceptos.append(Concepto("BAS" if tipo == "remunerativo" else "NR",
                                  desc_monto, detalle_monto, tipo, monto))
    antiguedad = redondear(monto * PORC_ANTIGUEDAD_ANUAL * anios)
    if antiguedad:
        liq.conceptos.append(Concepto(
            f"ANT{cod}", f"Antigüedad{sufijo}", f"{anios} años × 1% s/ $ {pesos(monto)}", tipo, antiguedad))
    if inasistencias:
        descuento = redondear((monto + antiguedad) / DIAS_MES * inasistencias)
        liq.conceptos.append(Concepto(
            f"INAS{cod}", f"Inasistencias injustificadas{sufijo}",
            f"{inasistencias} días × $ {pesos(monto + antiguedad)} / 30", tipo, -descuento))
    else:
        presentismo = redondear((monto + antiguedad) * PORC_PRESENTISMO)
        liq.conceptos.append(Concepto(
            f"PRES{cod}", f"Asistencia y puntualidad{sufijo}",
            f"{_pct(PORC_PRESENTISMO)} s/ $ {pesos(monto + antiguedad)}", tipo, presentismo))


def liquidar_comercio(
    *,
    periodo: str,
    categoria: str,
    basico: Decimal,
    vigencia_escala: date,
    fecha_ingreso: date,
    no_remunerativo: Decimal = Decimal("0"),
    jornada_horas: int = HORAS_JORNADA_COMPLETA,
    asignacion_extraordinaria: Decimal = Decimal("0"),
    inasistencias_injustificadas: int = 0,
    tope_base_imponible: Decimal | None = None,
) -> Liquidacion:
    """`basico`, `no_remunerativo` y `asignacion_extraordinaria` son montos de jornada completa."""
    if inasistencias_injustificadas < 0 or inasistencias_injustificadas > 30:
        raise ValueError("inasistencias_injustificadas tiene que estar entre 0 y 30")
    if not 1 <= jornada_horas <= HORAS_JORNADA_COMPLETA:
        raise ValueError("jornada_horas tiene que estar entre 1 y 8")
    if asignacion_extraordinaria < 0:
        raise ValueError("asignacion_extraordinaria no puede ser negativa")
    fin = ultimo_dia(periodo)
    if fecha_ingreso > fin:
        raise ValueError("el empleado ingresó después del período liquidado")

    factor = Decimal(jornada_horas) / HORAS_JORNADA_COMPLETA
    parcial = factor != 1
    anios = anios_cumplidos(fecha_ingreso, fin)
    liq = Liquidacion(
        periodo=periodo, categoria=categoria, basico_escala=basico,
        no_remunerativo_escala=no_remunerativo, vigencia_escala=vigencia_escala,
        jornada_horas=jornada_horas, anios_antiguedad=anios,
        dias_trabajados=30 - inasistencias_injustificadas,
    )
    jornada_txt = f"{jornada_horas} hs/día" if parcial else "jornada completa"

    _bloque(liq, tipo="remunerativo", sufijo="", monto=redondear(basico * factor),
            desc_monto="Sueldo básico", detalle_monto=f"{categoria} · {jornada_txt}",
            anios=anios, inasistencias=inasistencias_injustificadas)
    _bloque(liq, tipo="no_remunerativo", sufijo=" no rem.", monto=redondear(no_remunerativo * factor),
            desc_monto="Acuerdo no remunerativo", detalle_monto=f"Escala vigente · {jornada_txt}",
            anios=anios, inasistencias=inasistencias_injustificadas)
    extra = redondear(asignacion_extraordinaria * factor)
    if extra:
        liq.conceptos.append(Concepto(
            "EXTR", "Asignación extraordinaria", f"Única vez · {jornada_txt}", "no_remunerativo", extra))

    total_rem = sum((c.importe for c in liq.remunerativos()), Decimal("0"))
    total_nr = sum((c.importe for c in liq.no_remunerativos()), Decimal("0"))

    for codigo, desc, pct in APORTES_REMUNERATIVOS:
        base, detalle = total_rem, f"{_pct(pct)} s/ $ {pesos(total_rem)}"
        if tope_base_imponible is not None and base > tope_base_imponible:
            base, detalle = tope_base_imponible, f"{_pct(pct)} s/ tope $ {pesos(tope_base_imponible)}"
        liq.conceptos.append(Concepto(codigo, desc, detalle, "descuento", redondear(base * pct)))

    base_total = total_rem + total_nr
    for codigo, desc, pct, completa in APORTES_TOTALES:
        if completa and parcial:
            # Obra social de jornada parcial: aporte sobre la jornada completa (art. 92 ter LCT).
            base = base_total / factor
            detalle = f"{_pct(pct)} s/ $ {pesos(base)} (jornada completa equivalente)"
        else:
            base = base_total
            detalle = f"{_pct(pct)} s/ $ {pesos(base)}"
        liq.conceptos.append(Concepto(codigo, desc, detalle, "descuento", redondear(base * pct)))
        if codigo == "A101" and parcial:
            # Mismo criterio que el sistema actual: el Art. 101 se completa a jornada completa.
            compl = base_total * (1 / factor - 1)
            liq.conceptos.append(Concepto(
                "A101C", "Compl. Art. 101 CCT 130/75", f"{_pct(pct)} s/ $ {pesos(compl)}",
                "descuento", redondear(compl * pct)))

    total_desc = sum((c.importe for c in liq.descuentos()), Decimal("0"))
    neto = total_rem + total_nr - total_desc
    redondeo = neto.to_integral_value(rounding=ROUND_CEILING) - neto
    if redondeo:
        liq.conceptos.append(Concepto(
            "RED", "Redondeo", "Neto al peso entero", "no_remunerativo", redondeo))
        total_nr += redondeo
    liq.total_remunerativo = total_rem
    liq.total_no_remunerativo = total_nr
    liq.total_descuentos = total_desc
    liq.neto = total_rem + total_nr - total_desc
    return liq
