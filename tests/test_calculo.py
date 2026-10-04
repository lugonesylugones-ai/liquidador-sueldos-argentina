from datetime import date
from decimal import Decimal as D

import pytest

from backend.calculo import anios_cumplidos, liquidar_comercio


def liquidar(**kw):
    base = dict(periodo="2026-09", categoria="Administrativo A", basico=D("1000000"),
                vigencia_escala=date(2026, 7, 1), fecha_ingreso=date(2021, 1, 10))
    base.update(kw)
    return liquidar_comercio(**base)


def importes(liq):
    return {c.codigo: c.importe for c in liq.conceptos}


@pytest.mark.parametrize("desde,hasta,esperado", [
    (date(2020, 9, 30), date(2026, 9, 30), 6),   # cumple justo el último día del período
    (date(2020, 10, 1), date(2026, 9, 30), 5),
    (date(2024, 2, 29), date(2025, 2, 28), 0),
    (date(2026, 9, 1), date(2026, 9, 30), 0),
])
def test_anios_cumplidos(desde, hasta, esperado):
    assert anios_cumplidos(desde, hasta) == esperado


def test_liquidacion_sin_no_remunerativo():
    liq = liquidar()
    i = importes(liq)
    assert liq.anios_antiguedad == 5
    assert i["BAS"] == D("1000000.00")
    assert i["ANT"] == D("50000.00")                    # 5 × 1%
    assert i["PRES"] == D("87465.00")                   # 1.050.000 × 8,33%
    assert liq.total_remunerativo == D("1137465.00")
    assert i["JUB"] == D("125121.15")                   # 11%
    assert i["PAMI"] == D("34123.95")                   # 3%
    assert i["OS"] == D("34123.95")                     # 3%
    assert i["A101"] == i["A100"] == D("22749.30")      # 2% c/u
    assert i["FAECYS"] == D("5687.33")                  # 0,5% (5687.325 redondea para arriba)
    assert "NR" not in i and "A101C" not in i
    assert liq.total_descuentos == D("244554.98")
    assert liq.neto == D("892911.00")                   # 892.910,02 redondeado para arriba
    assert i["RED"] == D("0.98")


def test_no_remunerativo_lleva_antiguedad_y_presentismo_y_no_aporta_jubilacion():
    liq = liquidar(no_remunerativo=D("120000"))
    i = importes(liq)
    assert i["NR"] == D("120000.00")
    assert i["ANTNR"] == D("6000.00")                   # 5 × 1%
    assert i["PRESNR"] == D("10495.80")                 # 126.000 × 8,33%
    # Jubilación y PAMI solo sobre lo remunerativo
    assert i["JUB"] == D("125121.15")
    # OS, Art. 100, Art. 101 y FAECYS sobre todo
    assert i["OS"] == D("38218.82")                     # 3% de 1.273.960,80


def test_sin_antiguedad_no_muestra_concepto():
    liq = liquidar(fecha_ingreso=date(2026, 9, 1))
    assert "ANT" not in importes(liq)
    assert importes(liq)["PRES"] == D("83300.00")


def test_inasistencia_pierde_presentismo_y_descuenta_dias_en_ambos_bloques():
    liq = liquidar(inasistencias_injustificadas=2, no_remunerativo=D("120000"))
    i = importes(liq)
    assert "PRES" not in i and "PRESNR" not in i
    assert i["INAS"] == D("-70000.00")                  # 1.050.000 / 30 × 2
    assert i["INASNR"] == D("-8400.00")                 # 126.000 / 30 × 2
    assert liq.dias_trabajados == 28


def test_asignacion_extraordinaria_sin_antiguedad_ni_presentismo():
    i = importes(liquidar(asignacion_extraordinaria=D("25000"), jornada_horas=4))
    assert i["EXTR"] == D("12500.00")                   # proporcional a 4 hs
    assert "ANTNR" not in i


def test_jornada_parcial_completa_obra_social_y_art_101():
    i = importes(liquidar(jornada_horas=4))
    assert i["BAS"] == D("500000.00")
    total = D("500000") + D("25000") + D("43732.50")    # básico + antig + presentismo
    assert i["OS"] == (total * 2 * D("0.03")).quantize(D("0.01"))
    assert i["A101"] == i["A101C"] == (total * D("0.02")).quantize(D("0.01"))
    assert i["A100"] == (total * D("0.02")).quantize(D("0.01"))


def test_tope_base_imponible_solo_afecta_jubilacion_y_pami():
    i = importes(liquidar(tope_base_imponible=D("1000000")))
    assert i["JUB"] == D("110000.00")
    assert i["PAMI"] == D("30000.00")
    assert i["OS"] == D("34123.95")


def test_neto_cuadra_con_conceptos_y_es_entero():
    liq = liquidar(inasistencias_injustificadas=1, no_remunerativo=D("120000"),
                   asignacion_extraordinaria=D("25000"))
    rem = sum(c.importe for c in liq.remunerativos())
    nr = sum(c.importe for c in liq.no_remunerativos())
    desc = sum(c.importe for c in liq.descuentos())
    assert liq.neto == rem + nr - desc
    assert liq.neto == liq.neto.to_integral_value()


@pytest.mark.parametrize("kw", [
    {"fecha_ingreso": date(2026, 10, 1)},
    {"jornada_horas": 0},
    {"jornada_horas": 9},
    {"inasistencias_injustificadas": 31},
    {"asignacion_extraordinaria": D("-1")},
])
def test_datos_invalidos(kw):
    with pytest.raises(ValueError):
        liquidar(**kw)
