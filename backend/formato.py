"""Formatos argentinos: importes y números en letras."""
from decimal import Decimal, ROUND_HALF_UP

UNIDADES = ["", "uno", "dos", "tres", "cuatro", "cinco", "seis", "siete", "ocho", "nueve",
            "diez", "once", "doce", "trece", "catorce", "quince", "dieciséis", "diecisiete",
            "dieciocho", "diecinueve", "veinte", "veintiuno", "veintidós", "veintitrés",
            "veinticuatro", "veinticinco", "veintiséis", "veintisiete", "veintiocho", "veintinueve"]
DECENAS = ["", "", "", "treinta", "cuarenta", "cincuenta", "sesenta", "setenta", "ochenta", "noventa"]
CENTENAS = ["", "ciento", "doscientos", "trescientos", "cuatrocientos", "quinientos",
            "seiscientos", "setecientos", "ochocientos", "novecientos"]


def pesos(valor: Decimal) -> str:
    """1234567.8 -> '1.234.567,80'"""
    valor = Decimal(valor).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    texto = f"{abs(valor):,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    return f"-{texto}" if valor < 0 else texto


def _menor_mil(n: int) -> str:
    if n == 0:
        return ""
    if n == 100:
        return "cien"
    c, resto = divmod(n, 100)
    partes = [CENTENAS[c]] if c else []
    if resto < 30:
        if resto:
            partes.append(UNIDADES[resto])
    else:
        d, u = divmod(resto, 10)
        partes.append(DECENAS[d] + (f" y {UNIDADES[u]}" if u else ""))
    return " ".join(partes)


def _apocopar(texto: str) -> str:
    """'uno' delante de 'mil'/'millones' pasa a 'un' ('veintiuno' -> 'veintiún')."""
    if texto.endswith("veintiuno"):
        return texto[:-9] + "veintiún"
    if texto.endswith("uno"):
        return texto[:-3] + "un"
    return texto


def _entero_a_letras(n: int) -> str:
    if n == 0:
        return "cero"
    millones, resto = divmod(n, 1_000_000)
    miles, unidades = divmod(resto, 1000)
    partes = []
    if millones == 1:
        partes.append("un millón")
    elif millones:
        partes.append(_apocopar(_entero_a_letras(millones)) + " millones")
    if miles == 1:
        partes.append("mil")
    elif miles:
        partes.append(_apocopar(_menor_mil(miles)) + " mil")
    if unidades:
        partes.append(_menor_mil(unidades))
    return " ".join(partes)


def numero_a_letras(valor: Decimal) -> str:
    """Importe en letras como se usa en recibos: 'Pesos ... con 50/100'."""
    valor = Decimal(valor).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    if valor < 0:
        raise ValueError("no se puede expresar en letras un importe negativo")
    entero = int(valor)
    centavos = int((valor - entero) * 100)
    return f"Pesos {_entero_a_letras(entero)} con {centavos:02d}/100"
