"""Validación de CUIT y CUIL (mismo algoritmo de dígito verificador)."""
import re

_PESOS = (5, 4, 3, 2, 7, 6, 5, 4, 3, 2)


def digito_verificador(primeros_diez: str) -> int | None:
    resto = sum(int(d) * p for d, p in zip(primeros_diez, _PESOS)) % 11
    dv = 11 - resto
    if dv == 11:
        return 0
    if dv == 10:
        return None  # ARCA reasigna el prefijo en este caso; no hay CUIT válido
    return dv


def normalizar_cuit(texto) -> str:
    """'20123456786', '20-12345678-6' -> '20-12345678-6'. ValueError si no es válido."""
    digitos = re.sub(r"\D", "", str(texto or ""))
    if len(digitos) != 11:
        raise ValueError(f"CUIT/CUIL inválido {texto!r}: tiene que tener 11 dígitos")
    if digitos[:2] not in ("20", "23", "24", "25", "26", "27", "30", "33", "34"):
        raise ValueError(f"CUIT/CUIL inválido {texto!r}: prefijo {digitos[:2]} desconocido")
    if digito_verificador(digitos[:10]) != int(digitos[10]):
        raise ValueError(f"CUIT/CUIL inválido {texto!r}: el dígito verificador no coincide")
    return f"{digitos[:2]}-{digitos[2:10]}-{digitos[10]}"
