from datetime import date
from decimal import Decimal as D

import pytest

from backend.calculo import liquidar_comercio, liquidar_sac


def mes(periodo, basico, **kw):
    return liquidar_comercio(periodo=periodo, categoria="Auxiliar B", basico=D(basico),
                             no_remunerativo=D("120000"), vigencia_escala=date(2026, 7, 1),
                             fecha_ingreso=date(2017, 7, 3), **kw)


HISTORIAL = [
    mes("2026-07", "1161573", asignacion_extraordinaria=D("25000")),
    mes("2026-08", "1185469", asignacion_extraordinaria=D("25000")),
    mes("2026-09", "1209365"),
    mes("2026-06", "1500000"),   # otro semestre: no cuenta
]


def importes(liq):
    return {c.codigo: c.importe for c in liq.conceptos}


def test_sac_semestre_completo_usa_mejor_mes():
    liq = liquidar_sac(periodo="2026-12", categoria="Auxiliar B", fecha_ingreso=date(2017, 7, 3),
                       historial=HISTORIAL)
    i = importes(liq)
    assert liq.tipo == "sac"
    assert liq.basico_escala == D("1428014.56")          # septiembre, el mejor remunerativo
    assert i["SAC"] == D("714007.28")
    # No remunerativo habitual: sin la asignación de única vez ni el redondeo.
    assert liq.no_remunerativo_escala == D("141695.64")
    assert i["SACNR"] == D("70847.82")
    assert i["JUB"] == D("78540.80")                     # 11% del SAC remunerativo
    assert i["OS"] == D("23545.65")                      # 3% de SAC + SAC no rem.
    assert liq.neto == liq.neto.to_integral_value()


def test_sac_proporcional_por_ingreso_en_el_semestre():
    liq = liquidar_sac(periodo="2026-12", categoria="Auxiliar B", fecha_ingreso=date(2026, 10, 1),
                       historial=HISTORIAL)
    # 1/10 al 31/12 = 92 días de 184
    assert importes(liq)["SAC"] == D("357003.64")
    assert liq.dias_trabajados == 92


def test_sac_proporcional_por_egreso():
    liq = liquidar_sac(periodo="2026-09", categoria="Auxiliar B", fecha_ingreso=date(2017, 7, 3),
                       fecha_egreso=date(2026, 9, 30), historial=HISTORIAL)
    assert liq.dias_trabajados == 92
    assert importes(liq)["SAC"] == D("357003.64")


def test_sac_jornada_parcial_completa_obra_social():
    historial = [mes("2026-09", "1209365", jornada_horas=4)]
    i = importes(liquidar_sac(periodo="2026-12", categoria="Auxiliar B", fecha_ingreso=date(2017, 7, 3),
                              historial=historial, jornada_horas=4))
    assert "A101C" in i
    assert i["OS"] == ((i["SAC"] + i["SACNR"]) * 2 * D("0.03")).quantize(D("0.01"))


def test_sac_sin_meses_del_semestre():
    with pytest.raises(ValueError, match="no hay liquidaciones"):
        liquidar_sac(periodo="2026-12", categoria="Auxiliar B", fecha_ingreso=date(2017, 7, 3),
                     historial=[HISTORIAL[-1]])
