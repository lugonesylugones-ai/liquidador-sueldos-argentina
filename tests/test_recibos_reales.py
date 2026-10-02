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
