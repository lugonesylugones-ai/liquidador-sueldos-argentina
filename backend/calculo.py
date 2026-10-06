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

- Mes incompleto (ingreso o egreso en el mes): sueldo y no remunerativo × días / 30.

SAC: ver `liquidar_sac`. Liquidación final (egreso): ver `liquidar_final`.

Fuera de alcance (todavía): horas extra, vacaciones gozadas, licencias y ganancias.
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


def dias_del_mes(periodo: str, fecha_ingreso: date, fecha_egreso: date | None = None) -> int:
    """Días a pagar en el mes, sobre 30 (mes comercial): 30 si trabajó el mes entero."""
    inicio, fin = primer_dia(periodo), ultimo_dia(periodo)
    desde = max(inicio, fecha_ingreso)
    hasta = min(fin, fecha_egreso) if fecha_egreso else fin
    if hasta < desde:
        return 0
    if desde == inicio and hasta == fin:
        return 30
    return min(30, (hasta - desde).days + 1)


def _pct(valor: Decimal) -> str:
    texto = f"{valor * 100:.2f}".rstrip("0").rstrip(".")
    return texto.replace(".", ",") + "%"


@dataclass
class Concepto:
    codigo: str
    descripcion: str
    detalle: str          # cómo se determinó (art. 140 inc. c LCT)
    tipo: str             # "remunerativo" | "no_remunerativo" | "indemnizacion" | "descuento"
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
    tipo: str = "mensual"          # "mensual" | "sac" | "final"
    # Solo en la liquidación final: causa del egreso, fecha y si se otorgó preaviso.
    egreso: dict | None = None
    # Datos que no van en el recibo, como las contribuciones del empleador de cada convenio.
    informativos: list = field(default_factory=list)

    def de_tipo(self, tipo: str):
        return [c for c in self.conceptos if c.tipo == tipo]

    def remunerativos(self):
        return self.de_tipo("remunerativo")

    def no_remunerativos(self):
        return self.de_tipo("no_remunerativo")

    def descuentos(self):
        return self.de_tipo("descuento")

    def indemnizatorios(self):
        """Conceptos de la liquidación final que no llevan aportes (indemnizaciones, vacaciones no gozadas)."""
        return self.de_tipo("indemnizacion")

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
        datos.setdefault("egreso", None)
        datos["conceptos"] = [Concepto(**{**c, "importe": Decimal(c["importe"])}) for c in d["conceptos"]]
        datos["informativos"] = [Concepto(**{**c, "importe": Decimal(c["importe"])})
                                 for c in d.get("informativos", [])]
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
            f"ANT{cod}", f"Antigüedad{sufijo}",
            f"{anios} {'año' if anios == 1 else 'años'} × 1% s/ $ {pesos(monto)}", tipo, antiguedad))
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


def _haberes_comercio(
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
    dias: int = 30,
) -> Liquidacion:
    """Haberes del mes sin aportes. Ver `liquidar_comercio`."""
    if not 1 <= dias <= 30:
        raise ValueError("los días a liquidar tienen que estar entre 1 y 30")
    if inasistencias_injustificadas < 0 or inasistencias_injustificadas > dias:
        raise ValueError(f"inasistencias_injustificadas tiene que estar entre 0 y {dias}")
    if not 1 <= jornada_horas <= HORAS_JORNADA_COMPLETA:
        raise ValueError("jornada_horas tiene que estar entre 1 y 8")
    if asignacion_extraordinaria < 0:
        raise ValueError("asignacion_extraordinaria no puede ser negativa")
    fin = ultimo_dia(periodo)
    if fecha_ingreso > fin:
        raise ValueError("el empleado ingresó después del período liquidado")

    factor = Decimal(jornada_horas) / HORAS_JORNADA_COMPLETA
    parcial = factor != 1
    proporcion = Decimal(dias) / DIAS_MES
    anios = anios_cumplidos(fecha_ingreso, fin)
    liq = Liquidacion(
        periodo=periodo, categoria=categoria, basico_escala=basico,
        no_remunerativo_escala=no_remunerativo, vigencia_escala=vigencia_escala,
        jornada_horas=jornada_horas, anios_antiguedad=anios,
        dias_trabajados=dias - inasistencias_injustificadas,
    )
    jornada_txt = f"{jornada_horas} hs/día" if parcial else "jornada completa"
    if dias < 30:
        jornada_txt += f" · {dias}/30 días"

    _bloque(liq, tipo="remunerativo", sufijo="", monto=redondear(basico * factor * proporcion),
            desc_monto="Sueldo básico" if dias == 30 else "Sueldo proporcional",
            detalle_monto=f"{categoria} · {jornada_txt}",
            anios=anios, inasistencias=inasistencias_injustificadas)
    _bloque(liq, tipo="no_remunerativo", sufijo=" no rem.",
            monto=redondear(no_remunerativo * factor * proporcion),
            desc_monto="Acuerdo no remunerativo", detalle_monto=f"Escala vigente · {jornada_txt}",
            anios=anios, inasistencias=inasistencias_injustificadas)
    extra = redondear(asignacion_extraordinaria * factor)
    if extra:
        liq.conceptos.append(Concepto(
            "EXTR", "Asignación extraordinaria",
            f"Única vez · {jornada_txt.split(' · ')[0]}", "no_remunerativo", extra))
    return liq


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
    dias: int = 30,
) -> Liquidacion:
    """`basico`, `no_remunerativo` y `asignacion_extraordinaria` son montos de jornada completa.

    `dias` (sobre 30) es para el mes de ingreso o de egreso; ver `dias_del_mes`.
    """
    liq = _haberes_comercio(
        periodo=periodo, categoria=categoria, basico=basico, vigencia_escala=vigencia_escala,
        fecha_ingreso=fecha_ingreso, no_remunerativo=no_remunerativo, jornada_horas=jornada_horas,
        asignacion_extraordinaria=asignacion_extraordinaria,
        inasistencias_injustificadas=inasistencias_injustificadas, dias=dias)
    _aportes_y_neto(liq, Decimal(jornada_horas) / HORAS_JORNADA_COMPLETA, tope_base_imponible)
    return liq


def _aportes_y_neto(liq: Liquidacion, factor: Decimal, tope_base_imponible: Decimal | None) -> None:
    """Agrega aportes, redondeo y totales a partir de los haberes ya cargados."""
    parcial = factor != 1
    total_rem = sum((c.importe for c in liq.remunerativos()), Decimal("0"))
    total_nr = sum((c.importe for c in liq.no_remunerativos()), Decimal("0"))
    # Indemnizaciones y vacaciones no gozadas: sin aportes (art. 7 Ley 24.241).
    total_ind = sum((c.importe for c in liq.indemnizatorios()), Decimal("0"))

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
    total_nr += total_ind
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


# Conceptos no remunerativos que no son "normales y habituales" y no cuentan para el SAC.
NO_HABITUALES = {"EXTR", "RED"}


def semestre_de(periodo: str) -> tuple[date, date]:
    anio, mes = (int(p) for p in periodo.split("-"))
    if mes <= 6:
        return date(anio, 1, 1), date(anio, 6, 30)
    return date(anio, 7, 1), date(anio, 12, 31)


def _nr_habitual(liq: Liquidacion) -> Decimal:
    return sum((c.importe for c in liq.no_remunerativos() if c.codigo not in NO_HABITUALES), Decimal("0"))


def _agregar_sac(liq: Liquidacion, meses: list, inicio: date, fin: date, desde: date, hasta: date,
                 sufijo: str = "") -> int:
    """Agrega SAC y SAC s/ no remunerativo del semestre. Devuelve los días computados."""
    mejor_rem = max(meses, key=lambda l: l.total_remunerativo)
    mejor_nr = max(meses, key=_nr_habitual)
    dias = (hasta - desde).days + 1
    dias_semestre = (fin - inicio).days + 1
    proporcion = Decimal(dias) / Decimal(dias_semestre)
    prop_txt = "semestre completo" if dias == dias_semestre else f"{dias}/{dias_semestre} días"
    liq.conceptos.append(Concepto(
        "SAC", f"Sueldo anual complementario{sufijo}",
        f"50% de $ {pesos(mejor_rem.total_remunerativo)} ({mejor_rem.periodo}) · {prop_txt}",
        "remunerativo", redondear(mejor_rem.total_remunerativo / 2 * proporcion)))
    sac_nr = redondear(_nr_habitual(mejor_nr) / 2 * proporcion)
    if sac_nr:
        liq.conceptos.append(Concepto(
            "SACNR", f"SAC{sufijo} s/ no remunerativo",
            f"50% de $ {pesos(_nr_habitual(mejor_nr))} ({mejor_nr.periodo}) · {prop_txt}",
            "no_remunerativo", sac_nr))
    return dias


def liquidar_sac(
    *,
    periodo: str,
    categoria: str,
    fecha_ingreso: date,
    historial: list,
    jornada_horas: int = HORAS_JORNADA_COMPLETA,
    fecha_egreso: date | None = None,
    tope_base_imponible: Decimal | None = None,
    aportes=None,
) -> Liquidacion:
    """Sueldo anual complementario (Ley 23.041 y art. 121 LCT).

    `periodo` es el mes de pago (junio o diciembre). `historial` son las
    liquidaciones mensuales del empleado; se usan las del semestre de `periodo`.
    SAC = 50% de la mejor remuneración mensual del semestre, proporcional a los
    días trabajados en el semestre. La parte no remunerativa (acuerdo + antig. +
    presentismo) se trata igual y va como "SAC s/ no remunerativo"; la asignación
    de única vez y el redondeo no cuentan porque no son habituales.
    `aportes(liq)`: aportes de otro convenio; por defecto, los de Comercio.
    """
    inicio, fin = semestre_de(periodo)
    meses = [l for l in historial if l.tipo == "mensual" and inicio <= primer_dia(l.periodo) <= fin]
    if not meses:
        raise ValueError(f"no hay liquidaciones mensuales del semestre {inicio:%m/%Y}-{fin:%m/%Y}")
    desde = max(inicio, fecha_ingreso)
    hasta = min(fin, fecha_egreso) if fecha_egreso else fin
    if hasta < desde:
        raise ValueError("el empleado no trabajó en el semestre")

    mejor_rem = max(meses, key=lambda l: l.total_remunerativo)
    liq = Liquidacion(
        periodo=periodo, categoria=categoria, basico_escala=mejor_rem.total_remunerativo,
        no_remunerativo_escala=max(_nr_habitual(l) for l in meses), vigencia_escala=inicio,
        jornada_horas=jornada_horas, anios_antiguedad=anios_cumplidos(fecha_ingreso, hasta),
        dias_trabajados=0, tipo="sac",
    )
    liq.dias_trabajados = _agregar_sac(liq, meses, inicio, fin, desde, hasta)
    if aportes:
        aportes(liq)
    else:
        _aportes_y_neto(liq, Decimal(jornada_horas) / HORAS_JORNADA_COMPLETA, tope_base_imponible)
    return liq


# --- Liquidación final ---------------------------------------------------------
CAUSAS_EGRESO = {
    "renuncia": "Renuncia (art. 240 LCT)",
    "despido_sin_causa": "Despido sin causa (art. 245 LCT)",
    "despido_con_causa": "Despido con causa (art. 242 LCT)",
    "mutuo_acuerdo": "Mutuo acuerdo (art. 241 LCT)",
    "fallecimiento": "Fallecimiento del trabajador (art. 248 LCT)",
}
# Ley 27.742 (B.O. 08/07/2024) llevó el período de prueba de 3 a 6 meses.
INICIO_PRUEBA_6_MESES = date(2024, 7, 9)


def _sumar_meses(d: date, meses: int) -> date:
    total = d.month - 1 + meses
    anio, mes = d.year + total // 12, total % 12 + 1
    return date(anio, mes, min(d.day, monthrange(anio, mes)[1]))


def dias_vacaciones_anuales(fecha_ingreso: date, anio: int) -> int:
    """Art. 150 LCT: según la antigüedad al 31/12 del año."""
    anios = anios_cumplidos(fecha_ingreso, date(anio, 12, 31))
    if anios < 5:
        return 14
    if anios < 10:
        return 21
    if anios < 20:
        return 28
    return 35


def anios_indemnizacion(fecha_ingreso: date, fecha_egreso: date) -> int:
    """Art. 245 LCT: un mes por año de servicio o fracción mayor de tres meses."""
    anios = anios_cumplidos(fecha_ingreso, fecha_egreso)
    aniversario = _sumar_meses(fecha_ingreso, 12 * anios)
    if fecha_egreso > _sumar_meses(aniversario, 3):
        anios += 1
    return anios


def en_periodo_de_prueba(fecha_ingreso: date, fecha_egreso: date) -> bool:
    meses = 6 if fecha_ingreso >= INICIO_PRUEBA_6_MESES else 3
    return fecha_egreso < _sumar_meses(fecha_ingreso, meses)


def liquidar_final(
    *,
    categoria: str,
    basico: Decimal,
    vigencia_escala: date,
    fecha_ingreso: date,
    fecha_egreso: date,
    causa: str,
    historial: list,
    no_remunerativo: Decimal = Decimal("0"),
    jornada_horas: int = HORAS_JORNADA_COMPLETA,
    asignacion_extraordinaria: Decimal = Decimal("0"),
    inasistencias_injustificadas: int = 0,
    preaviso_otorgado: bool = False,
    vacaciones_gozadas: Decimal = Decimal("0"),
    tope_indemnizatorio: Decimal | None = None,
    tope_base_imponible: Decimal | None = None,
) -> Liquidacion:
    """Liquidación final del mes del egreso.

    Incluye: días trabajados del mes, SAC proporcional del semestre, vacaciones
    no gozadas proporcionales con su SAC (art. 156 LCT) y, según la causa,
    indemnización por antigüedad (art. 245, o 50% por fallecimiento, art. 248),
    preaviso (art. 231/232) e integración del mes de despido (art. 233), cada uno
    con su SAC salvo la indemnización. Las indemnizaciones y vacaciones no gozadas
    no llevan aportes. `historial` son las liquidaciones mensuales anteriores.
    """
    if causa not in CAUSAS_EGRESO:
        raise ValueError(f"causa de egreso desconocida: {causa}")
    if fecha_egreso < fecha_ingreso:
        raise ValueError("la fecha de egreso es anterior a la de ingreso")
    periodo = fecha_egreso.strftime("%Y-%m")
    factor = Decimal(jornada_horas) / HORAS_JORNADA_COMPLETA
    dias = dias_del_mes(periodo, fecha_ingreso, fecha_egreso)

    # 1) Días trabajados del mes.
    liq = _haberes_comercio(
        periodo=periodo, categoria=categoria, basico=basico, vigencia_escala=vigencia_escala,
        fecha_ingreso=fecha_ingreso, no_remunerativo=no_remunerativo, jornada_horas=jornada_horas,
        asignacion_extraordinaria=asignacion_extraordinaria,
        inasistencias_injustificadas=inasistencias_injustificadas, dias=dias)
    liq.tipo = "final"
    liq.anios_antiguedad = anios_cumplidos(fecha_ingreso, fecha_egreso)
    liq.egreso = {"fecha": fecha_egreso.isoformat(), "causa": causa,
                  "preaviso_otorgado": preaviso_otorgado}

    # Remuneración mensual normal y habitual de un mes completo (base de vacaciones,
    # preaviso e integración): la de este mes como si se hubiera trabajado entero.
    mes_completo = _haberes_comercio(
        periodo=periodo, categoria=categoria, basico=basico, vigencia_escala=vigencia_escala,
        fecha_ingreso=fecha_ingreso, no_remunerativo=no_remunerativo, jornada_horas=jornada_horas)
    rem_mes = sum((c.importe for c in mes_completo.remunerativos()), Decimal("0"))
    nr_mes = _nr_habitual(mes_completo)
    mes_completo.total_remunerativo = rem_mes

    # 2) SAC proporcional: mejor mes del semestre (incluido el mes del egreso).
    inicio, fin = semestre_de(periodo)
    liq.total_remunerativo = sum((c.importe for c in liq.remunerativos()), Decimal("0"))
    meses = [l for l in historial if l.tipo == "mensual" and inicio <= primer_dia(l.periodo) <= fin
             and l.periodo != periodo] + [liq]
    _agregar_sac(liq, meses, inicio, fin, max(inicio, fecha_ingreso), fecha_egreso, " proporcional")

    # 3) Vacaciones no gozadas proporcionales (arts. 150, 155 y 156 LCT): valor día = mes / 25.
    anio = fecha_egreso.year
    dias_anio = (date(anio, 12, 31) - date(anio, 1, 1)).days + 1
    trabajados = (fecha_egreso - max(fecha_ingreso, date(anio, 1, 1))).days + 1
    corresponden = dias_vacaciones_anuales(fecha_ingreso, anio)
    dias_vac = redondear(Decimal(corresponden) * trabajados / dias_anio) - vacaciones_gozadas
    if dias_vac > 0:
        detalle = (f"{pesos(dias_vac)} días ({corresponden} × {trabajados}/{dias_anio}"
                   f"{f' − {pesos(vacaciones_gozadas)} gozados' if vacaciones_gozadas else ''})"
                   f" × $ {{}} / 25")
        vac = redondear(rem_mes / 25 * dias_vac)
        liq.conceptos.append(Concepto("VAC", "Vacaciones no gozadas", detalle.format(pesos(rem_mes)),
                                      "indemnizacion", vac))
        vac_nr = redondear(nr_mes / 25 * dias_vac)
        if vac_nr:
            liq.conceptos.append(Concepto("VACNR", "Vacaciones no gozadas s/ no remunerativo",
                                          detalle.format(pesos(nr_mes)), "indemnizacion", vac_nr))
        liq.conceptos.append(Concepto("SACVAC", "SAC s/ vacaciones no gozadas",
                                      f"$ {pesos(vac + vac_nr)} / 12", "indemnizacion",
                                      redondear((vac + vac_nr) / 12)))

    # 4) Indemnizaciones por despido.
    base_mes = rem_mes + nr_mes
    if causa in ("despido_sin_causa", "fallecimiento"):
        prueba = en_periodo_de_prueba(fecha_ingreso, fecha_egreso)
        if not prueba:
            # Mejor remuneración mensual normal y habitual del último año (sin SAC).
            desde = _sumar_meses(primer_dia(periodo), -12)
            candidatos = [l.total_remunerativo + _nr_habitual(l) for l in historial
                          if l.tipo == "mensual" and l.dias_trabajados >= 30
                          and desde <= primer_dia(l.periodo) < primer_dia(periodo)]
            base = max(candidatos + [base_mes])
            base_txt = f"$ {pesos(base)}"
            if tope_indemnizatorio is not None and base > tope_indemnizatorio:
                # Tope del art. 245 con el piso del 67% de la remuneración (fallo Vizzoti).
                topeada = max(tope_indemnizatorio, redondear(base * Decimal("0.67")))
                base_txt = f"$ {pesos(topeada)} (tope s/ $ {pesos(base)})"
                base = topeada
            anios = max(1, anios_indemnizacion(fecha_ingreso, fecha_egreso))
            indem = redondear(base * anios)
            detalle = f"{anios} {'año' if anios == 1 else 'años'} × {base_txt}"
            if causa == "fallecimiento":
                indem = redondear(indem / 2)
                detalle = f"50% de {detalle}"
            liq.conceptos.append(Concepto("IND", "Indemnización por antigüedad", detalle,
                                          "indemnizacion", indem))
        if causa == "despido_sin_causa" and not preaviso_otorgado:
            if prueba:
                meses_preaviso, txt = Decimal("0.5"), "15 días (período de prueba)"
            elif anios_cumplidos(fecha_ingreso, fecha_egreso) < 5:
                meses_preaviso, txt = Decimal("1"), "1 mes"
            else:
                meses_preaviso, txt = Decimal("2"), "2 meses"
            preaviso = redondear(base_mes * meses_preaviso)
            liq.conceptos.append(Concepto("PREAV", "Indemnización sustitutiva de preaviso",
                                          f"{txt} × $ {pesos(base_mes)}", "indemnizacion", preaviso))
            liq.conceptos.append(Concepto("SACPREAV", "SAC s/ preaviso", f"$ {pesos(preaviso)} / 12",
                                          "indemnizacion", redondear(preaviso / 12)))
            faltan = 30 - dias
            if faltan > 0:
                integ = redondear(base_mes / DIAS_MES * faltan)
                liq.conceptos.append(Concepto("INTEG", "Integración mes de despido",
                                              f"{faltan} días × $ {pesos(base_mes)} / 30",
                                              "indemnizacion", integ))
                liq.conceptos.append(Concepto("SACINTEG", "SAC s/ integración mes de despido",
                                              f"$ {pesos(integ)} / 12", "indemnizacion",
                                              redondear(integ / 12)))

    _aportes_y_neto(liq, factor, tope_base_imponible)
    return liq
