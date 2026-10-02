from decimal import Decimal as D

import pytest

from backend.formato import numero_a_letras, pesos


@pytest.mark.parametrize("valor,texto", [
    ("0", "Pesos cero con 00/100"),
    ("1", "Pesos uno con 00/100"),
    ("100", "Pesos cien con 00/100"),
    ("115.5", "Pesos ciento quince con 50/100"),
    ("21000", "Pesos veintiún mil con 00/100"),
    ("31001", "Pesos treinta y un mil uno con 00/100"),
    ("1000000", "Pesos un millón con 00/100"),
    ("2500000", "Pesos dos millones quinientos mil con 00/100"),
    ("938408.62", "Pesos novecientos treinta y ocho mil cuatrocientos ocho con 62/100"),
])
def test_numero_a_letras(valor, texto):
    assert numero_a_letras(D(valor)) == texto


def test_pesos():
    assert pesos(D("1234567.891")) == "1.234.567,89"
    assert pesos(D("-70000")) == "-70.000,00"
