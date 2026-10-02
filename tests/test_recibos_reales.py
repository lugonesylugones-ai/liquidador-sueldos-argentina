"""Casos de oro: recibos reales de septiembre 2026 (Auxiliar B), anonimizados.

Cada caso reproduce al centavo el recibo que emitió el sistema actual. El Día
del Empleado de Comercio aparece en esos recibos como +jornal / -jornal y no
cambia ningún total, por eso no se modela.
"""
from datetime import date
from decimal import Decimal as D

import pytest

from backend.calculo import liquidar_comercio

ESCALA_SEP_2026 = dict(categoria="Auxiliar B", basico=D("1209365"), no_remunerativo=D("120000"),
                       vigencia_escala=date(2026, 9, 1), periodo="2026-09")

CASOS = {
    "Empleado A, 8 hs, 9 años": dict(
        fecha_ingreso=date(2017, 7, 3), jornada_horas=8,
        esperado={"BAS": "1209365.00", "ANT": "108842.85", "PRES": "109806.71",
                  "NR": "120000.00", "ANTNR": "10800.00", "PRESNR": "10895.64",
                  "JUB": "157081.60", "PAMI": "42840.44", "OS": "47091.31",
                  "A101": "31394.20", "A100": "31394.20", "FAECYS": "7848.55", "RED": "0.10"},
        totales=("1428014.56", "141695.74", "317650.30", "1252060.00")),
    "Empleado B, 8 hs, 4 años": dict(
        fecha_ingreso=date(2022, 9, 1), jornada_horas=8,
        esperado={"BAS": "1209365.00", "ANT": "48374.60", "PRES": "104769.71",
                  "NR": "120000.00", "ANTNR": "4800.00", "PRESNR": "10395.84",
                  "JUB": "149876.02", "PAMI": "40875.28", "OS": "44931.15",
                  "A101": "29954.10", "A100": "29954.10", "FAECYS": "7488.53", "RED": "0.03"},
        totales=("1362509.31", "135195.87", "303079.18", "1194626.00")),
    "Empleado C, 4 hs, 21 años": dict(
        fecha_ingreso=date(2004, 12, 20), jornada_horas=4,
        esperado={"BAS": "604682.50", "ANT": "126983.33", "PRES": "60947.76",
                  "NR": "60000.00", "ANTNR": "12600.00", "PRESNR": "6047.58",
                  "JUB": "87187.49", "PAMI": "23778.41", "OS": "52275.67",
                  "A101": "17425.22", "A101C": "17425.22", "A100": "17425.22",
                  "FAECYS": "4356.31", "RED": "0.37"},
        totales=("792613.59", "78647.95", "219873.54", "651388.00")),
}


@pytest.mark.parametrize("nombre", CASOS)
def test_reproduce_recibo_real(nombre):
    caso = CASOS[nombre]
    liq = liquidar_comercio(**ESCALA_SEP_2026, fecha_ingreso=caso["fecha_ingreso"],
                            jornada_horas=caso["jornada_horas"])
    assert {c.codigo: str(c.importe) for c in liq.conceptos} == caso["esperado"]
    assert (str(liq.total_remunerativo), str(liq.total_no_remunerativo),
            str(liq.total_descuentos), str(liq.neto)) == caso["totales"]


# Julio y agosto 2026: se comparan totales (remunerativo, no rem., descuentos, neto).
# A = 8 hs, ingreso 03/07/2017; B = 8 hs, ingreso 01/09/2022; C = 4 hs, ingreso 20/12/2004.
INGRESOS = {"A": (date(2017, 7, 3), 8), "B": (date(2022, 9, 1), 8), "C": (date(2004, 12, 20), 4)}
ESCALAS_MES = {
    "2026-07": D("1161573"),
    "2026-08": D("1185469"),
}
TOTALES_JUL_AGO = [
    ("A", "2026-07", ("1371581.91", "166696.38", "307392.29", "1230886.00")),
    ("B", "2026-07", ("1296081.99", "158896.84", "290574.83", "1164404.00")),
    ("A", "2026-08", ("1399798.24", "166696.57", "313458.81", "1253036.00")),
    ("B", "2026-08", ("1322745.12", "158896.27", "296307.39", "1185334.00")),
    ("C", "2026-08", ("776952.89", "91148.08", "217285.97", "650815.00")),
]


@pytest.mark.parametrize("empleado,periodo,totales", TOTALES_JUL_AGO)
def test_reproduce_totales_julio_agosto(empleado, periodo, totales):
    ingreso, horas = INGRESOS[empleado]
    basico = ESCALAS_MES[periodo]
    if horas == 4:
        # El sistema actual tenía la categoría de 4 hs cargada a mano en 592.735,00
        # (la mitad exacta es 592.734,50); se replica para comparar.
        basico = D("1185470")
    liq = liquidar_comercio(periodo=periodo, categoria="Auxiliar B", basico=basico,
                            no_remunerativo=D("120000"), vigencia_escala=date(2026, 7, 1),
                            fecha_ingreso=ingreso, jornada_horas=horas,
                            asignacion_extraordinaria=D("25000"))
    assert (str(liq.total_remunerativo), str(liq.total_no_remunerativo),
            str(liq.total_descuentos), str(liq.neto)) == totales
