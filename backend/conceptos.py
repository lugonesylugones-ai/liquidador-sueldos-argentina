"""Conceptos que carga el usuario (plus, premios, sumas no remunerativas, descuentos) y su fórmula.

Cada concepto dice qué es (remunerativo, no remunerativo o descuento) y cómo se calcula:

- `fijo`: un importe por mes (`valor`), proporcional a la jornada y a los días del mes si se pide.
- `porcentaje`: `valor`% de la suma de otros conceptos del recibo (`base`: códigos como BAS, ANT,
  PRES o de otros conceptos propios; o TOTAL_REM, TOTAL_NR, BRUTO).
- `cantidad`: `valor` × la cantidad del empleado en el mes (horas, días, unidades).
- `formula`: una cuenta libre con los códigos del recibo y las variables de `VARIABLES`, por
  ejemplo `BAS / 200 * 1.5 * CANTIDAD`. Solo números (con punto decimal), + - * / ( ), min, max,
  abs y redondear.

Los haberes (remunerativos y no remunerativos) se agregan antes de los aportes, así que pagan
los aportes que correspondan; los descuentos se agregan después, con el resto de los descuentos.
Se calculan en el orden de `orden`: un concepto puede usar a los que van antes que él.
"""
import ast
import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

from .calculo import Concepto, Liquidacion, redondear, _pct
from .formato import pesos

TIPOS = {"remunerativo": "Remunerativo", "no_remunerativo": "No remunerativo", "descuento": "Descuento"}
CALCULOS = {"fijo": "Suma fija", "porcentaje": "Porcentaje de otros conceptos",
            "cantidad": "Valor × cantidad", "formula": "Fórmula libre"}
# Variables que puede usar una fórmula, además de los códigos de los conceptos del recibo.
VARIABLES = {
    "TOTAL_REM": "Total remunerativo calculado hasta ese momento",
    "TOTAL_NR": "Total no remunerativo calculado hasta ese momento",
    "BRUTO": "Remunerativo + no remunerativo",
    "VALOR": "El valor del concepto (el del empleado, si tiene uno propio)",
    "CANTIDAD": "La cantidad del empleado en el mes (horas, días, unidades)",
    "BASICO_ESCALA": "Básico de la escala para la categoría, a jornada completa",
    "HORAS": "Horas de la jornada diaria del empleado",
    "DIAS": "Días pagados del mes, sobre 30",
    "ANIOS": "Años de antigüedad",
}
CODIGO_VALIDO = re.compile(r"^[A-Z][A-Z0-9]{1,9}$")
_FUNCIONES = {"min": min, "max": max, "redondear": redondear, "abs": abs}


class ErrorFormula(ValueError):
    pass


def _analizar(formula: str) -> ast.Expression:
    try:
        arbol = ast.parse(formula, mode="eval")
    except SyntaxError:
        raise ErrorFormula(f"La fórmula «{formula}» no se entiende") from None
    for nodo in ast.walk(arbol):
        if isinstance(nodo, ast.Call):
            if not (isinstance(nodo.func, ast.Name) and nodo.func.id in _FUNCIONES) or nodo.keywords:
                raise ErrorFormula(f"En las fórmulas solo valen las funciones {', '.join(_FUNCIONES)}")
        elif isinstance(nodo, ast.Constant):
            if not isinstance(nodo.value, (int, float)) or isinstance(nodo.value, bool):
                raise ErrorFormula("Las fórmulas solo llevan números, códigos y + - * / ( )")
        elif not isinstance(nodo, (ast.Expression, ast.BinOp, ast.UnaryOp, ast.Name, ast.Load,
                                   ast.Add, ast.Sub, ast.Mult, ast.Div, ast.USub, ast.UAdd)):
            raise ErrorFormula("Las fórmulas solo llevan números, códigos y + - * / ( )")
    return arbol


def nombres_de(formula: str) -> set:
    """Los códigos y variables que usa una fórmula (para validarla al guardarla)."""
    return {n.id for n in ast.walk(_analizar(formula)) if isinstance(n, ast.Name) and n.id not in _FUNCIONES}


def evaluar(formula: str, valores: dict) -> Decimal:
    """Calcula la fórmula con Decimal. Un código que no está en el recibo vale 0."""
    def ev(n):
        if isinstance(n, ast.Expression):
            return ev(n.body)
        if isinstance(n, ast.Constant):
            return Decimal(str(n.value))
        if isinstance(n, ast.Name):
            return Decimal(valores.get(n.id, 0))
        if isinstance(n, ast.UnaryOp):
            v = ev(n.operand)
            return -v if isinstance(n.op, ast.USub) else v
        if isinstance(n, ast.Call):
            return Decimal(_FUNCIONES[n.func.id](*(ev(a) for a in n.args)))
        a, b = ev(n.left), ev(n.right)
        if isinstance(n.op, ast.Add):
            return a + b
        if isinstance(n.op, ast.Sub):
            return a - b
        if isinstance(n.op, ast.Mult):
            return a * b
        if b == 0:
            raise ErrorFormula("La fórmula divide por cero")
        return a / b
    try:
        return ev(_analizar(formula))
    except (InvalidOperation, TypeError) as exc:
        raise ErrorFormula(f"No se pudo calcular «{formula}»: {exc}") from None


@dataclass
class Definicion:
    """Un concepto propio, ya resuelto para un empleado (con su valor y cantidad)."""
    codigo: str
    descripcion: str
    tipo: str                     # remunerativo | no_remunerativo | descuento
    calculo: str                  # fijo | porcentaje | cantidad | formula
    valor: Decimal = Decimal("0")
    base: tuple = ()              # códigos que suma el porcentaje
    formula: str = ""
    proporcional: bool = True     # fijo: × horas/8 × días/30
    habitual: bool = True         # cuenta para SAC, vacaciones e indemnizaciones
    aportes: bool = True          # no remunerativo: paga obra social y aportes sindicales (Comercio)
    cantidad: Decimal = Decimal("0")

    def importe_y_detalle(self, valores: dict, *, horas: int, dias: int) -> tuple[Decimal, str]:
        if self.calculo == "fijo":
            importe, detalle = self.valor, "Suma fija"
            if self.proporcional and (horas != 8 or dias != 30):
                importe = self.valor * Decimal(horas) / 8 * Decimal(dias) / 30
                partes = ([f"{horas}/8 hs"] if horas != 8 else []) + ([f"{dias}/30 días"] if dias != 30 else [])
                detalle = f"$ {pesos(self.valor)} × {' × '.join(partes)}"
            return importe, detalle
        if self.calculo == "porcentaje":
            base = sum((Decimal(valores.get(c, 0)) for c in self.base), Decimal("0"))
            return base * self.valor / 100, f"{_pct(self.valor / 100)} s/ $ {pesos(redondear(base))}"
        if self.calculo == "cantidad":
            return (self.valor * self.cantidad,
                    f"{pesos(self.cantidad).removesuffix(',00')} × $ {pesos(self.valor)}")
        return evaluar(self.formula, valores), self.formula


def valores_del_recibo(liq: Liquidacion) -> dict:
    """Los importes del recibo por código, más los totales que usan las fórmulas."""
    v = {}
    for c in liq.conceptos:
        v[c.codigo] = v.get(c.codigo, Decimal("0")) + c.importe
    rem = sum((c.importe for c in liq.remunerativos()), Decimal("0"))
    nr = sum((c.importe for c in liq.no_remunerativos() if c.codigo != "RED"), Decimal("0"))
    v.update(TOTAL_REM=rem, TOTAL_NR=nr, BRUTO=rem + nr, BASICO_ESCALA=liq.basico_escala,
             HORAS=Decimal(liq.jornada_horas), ANIOS=Decimal(liq.anios_antiguedad))
    return v


def aplicar_haberes(liq: Liquidacion, definiciones: list, *, dias: int) -> None:
    """Agrega al recibo los conceptos propios remunerativos y no remunerativos (antes de los aportes)."""
    for d in definiciones:
        if d.tipo == "descuento":
            continue
        valores = {**valores_del_recibo(liq), "VALOR": d.valor, "CANTIDAD": d.cantidad, "DIAS": Decimal(dias)}
        importe, detalle = d.importe_y_detalle(valores, horas=liq.jornada_horas, dias=dias)
        importe = redondear(importe)
        if importe:
            liq.conceptos.append(Concepto(d.codigo, d.descripcion, detalle, d.tipo, importe,
                                          habitual=d.habitual,
                                          aportes=d.aportes or d.tipo == "remunerativo"))


def descuentos(liq: Liquidacion, definiciones: list, *, principal: bool) -> list:
    """Los descuentos propios como (código, descripción, detalle, importe) para
    `aplicar_descuentos_varios`. Los fijos y por cantidad, solo en el recibo principal
    (sueldo o final); los porcentajes y fórmulas, en todos los recibos."""
    items = []
    dias = Decimal(liq.dias_trabajados)
    for d in definiciones:
        if d.tipo != "descuento" or (d.calculo in ("fijo", "cantidad") and not principal):
            continue
        valores = {**valores_del_recibo(liq), "VALOR": d.valor, "CANTIDAD": d.cantidad, "DIAS": dias}
        importe, detalle = d.importe_y_detalle(valores, horas=liq.jornada_horas, dias=30)
        items.append((d.codigo, d.descripcion, detalle, redondear(importe)))
    return items
