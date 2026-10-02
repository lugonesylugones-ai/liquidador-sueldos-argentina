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


def test_liquidacion_completa_sin_afiliacion():
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
    assert i["FAECYS"] == D("5687.33")                  # 0,5% (5687.325 redondea para arriba)
    assert "SIND" not in i
    assert liq.total_descuentos == D("199056.38")
    assert liq.neto == D("938408.62")


def test_afiliado_paga_cuota_sindical():
    i = importes(liquidar(afiliado_sindicato=True))
    assert i["SIND"] == D("22749.30")                   # 2% de 1.137.465


def test_sin_antiguedad_no_muestra_concepto():
    liq = liquidar(fecha_ingreso=date(2026, 9, 1))
    assert "ANT" not in importes(liq)
    assert importes(liq)["PRES"] == D("83300.00")


def test_inasistencia_injustificada_pierde_presentismo_y_descuenta_dias():
    liq = liquidar(inasistencias_injustificadas=2)
    i = importes(liq)
    assert "PRES" not in i
    assert i["INAS"] == D("-70000.00")                  # 1.050.000 / 30 × 2
    assert liq.total_remunerativo == D("980000.00")
    assert liq.dias_trabajados == 28


def test_tope_base_imponible_solo_afecta_jub_pami_os():
    liq = liquidar(tope_base_imponible=D("1000000"), afiliado_sindicato=True)
    i = importes(liq)
    assert i["JUB"] == D("110000.00")
    assert i["PAMI"] == D("30000.00")
    assert i["OS"] == D("30000.00")
    assert i["FAECYS"] == D("5687.33")                  # sin tope
    assert i["SIND"] == D("22749.30")                   # sin tope


def test_neto_cuadra_con_conceptos():
    liq = liquidar(afiliado_sindicato=True, inasistencias_injustificadas=1)
    rem = sum(c.importe for c in liq.remunerativos())
    desc = sum(c.importe for c in liq.descuentos())
    assert liq.neto == rem - desc


def test_ingreso_posterior_al_periodo_falla():
    with pytest.raises(ValueError):
        liquidar(fecha_ingreso=date(2026, 10, 1))
