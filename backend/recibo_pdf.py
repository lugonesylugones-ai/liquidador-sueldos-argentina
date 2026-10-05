"""Recibo de sueldo en PDF con los requisitos del art. 140 LCT.

Cada inciso del art. 140 está marcado en el código para poder auditarlo:
  a) empleador: nombre, domicilio, CUIT
  b) trabajador: nombre, calificación profesional, CUIL
  c) remuneraciones con indicación de su determinación
  d) art. 12 dec.-ley 17.250: último depósito de aportes (período, fecha, banco)
  e) total bruto y tiempo que corresponde
  f) deducciones por aportes y demás descuentos
  g) neto en números y letras
  h) constancia de recepción del duplicado
  i) lugar y fecha del pago
  k) fecha de ingreso y categoría
(El inc. j solo aplica a pagos supervisados por la autoridad, arts. 124 y 129.)
"""
from dataclasses import dataclass
from datetime import date

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from .calculo import CAUSAS_EGRESO, Liquidacion
from .formato import numero_a_letras, pesos

MESES = ["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto",
         "septiembre", "octubre", "noviembre", "diciembre"]


@dataclass
class DatosRecibo:
    empresa_razon_social: str
    empresa_cuit: str
    empresa_domicilio: str
    empleado_apellido: str
    empleado_nombre: str
    empleado_cuil: str
    empleado_fecha_ingreso: date
    convenio: str
    liquidacion: Liquidacion
    fecha_pago: date
    lugar_pago: str
    ultimo_deposito_periodo: str
    ultimo_deposito_fecha: date
    ultimo_deposito_banco: str
    empleado_legajo: str | None = None


def _periodo_texto(periodo: str) -> str:
    anio, mes = periodo.split("-")
    return f"{MESES[int(mes) - 1].capitalize()} {anio}"


def _f(d: date) -> str:
    return d.strftime("%d/%m/%Y")


def _anios(liq: Liquidacion) -> str:
    return f"{liq.anios_antiguedad} {'año' if liq.anios_antiguedad == 1 else 'años'}"


def _filas_periodo(liq: Liquidacion, normal) -> list:
    if liq.tipo == "final":
        eg = liq.egreso or {}
        causa = CAUSAS_EGRESO.get(eg.get("causa"), eg.get("causa", ""))
        preaviso = "otorgado" if eg.get("preaviso_otorgado") else "no otorgado"
        return [
            [Paragraph(f"<b>Período:</b> {_periodo_texto(liq.periodo)} · liquidación final", normal),
             Paragraph(f"<b>Días trabajados:</b> {liq.dias_trabajados}", normal)],
            [Paragraph(f"<b>Egreso:</b> {_f(date.fromisoformat(eg['fecha']))} · {causa}", normal),
             Paragraph(f"<b>Antigüedad:</b> {_anios(liq)}", normal)],
            [Paragraph(f"<b>Básico de escala:</b> $ {pesos(liq.basico_escala)} "
                       f"(vigente desde {_f(liq.vigencia_escala)})", normal),
             Paragraph(f"<b>Preaviso:</b> {preaviso}", normal)],
        ]
    if liq.tipo == "sac":
        semestre = "1°" if int(liq.periodo[5:]) <= 6 else "2°"
        return [
            [Paragraph(f"<b>Período:</b> SAC {semestre} semestre {liq.periodo[:4]}", normal),
             Paragraph(f"<b>Días del semestre:</b> {liq.dias_trabajados}", normal)],
            [Paragraph(f"<b>Mejor remuneración del semestre:</b> $ {pesos(liq.basico_escala)}", normal),
             Paragraph(f"<b>Antigüedad:</b> {_anios(liq)}", normal)],
            [Paragraph(f"<b>Mejor no remunerativo habitual:</b> $ {pesos(liq.no_remunerativo_escala)}", normal),
             ""],
        ]
    return [
        [Paragraph(f"<b>Período:</b> {_periodo_texto(liq.periodo)} · {'zona fría' if liq.tipo == 'zona_fria' else 'mensual'}", normal),
         Paragraph(f"<b>Días trabajados:</b> {liq.dias_trabajados}", normal)],
        [Paragraph(f"<b>Básico de escala:</b> $ {pesos(liq.basico_escala)} "
                   f"(vigente desde {_f(liq.vigencia_escala)})", normal),
         Paragraph(f"<b>Antigüedad:</b> {_anios(liq)}", normal)],
        [Paragraph(f"<b>No remunerativo de escala:</b> $ {pesos(liq.no_remunerativo_escala)}", normal),
         ""],
    ]


def _copia(d: DatosRecibo, leyenda: str, estilos) -> list:
    liq = d.liquidacion
    chico = ParagraphStyle("chico", parent=estilos["Normal"], fontSize=8, leading=10)
    normal = ParagraphStyle("normal", parent=estilos["Normal"], fontSize=9, leading=11)
    titulo = ParagraphStyle("titulo", parent=estilos["Title"], fontSize=13, spaceAfter=2)
    grilla = colors.HexColor("#9AA5B1")
    gris = colors.HexColor("#E8ECF0")

    elementos = [
        Table([[Paragraph("<b>RECIBO DE HABERES</b> · Ley 20.744 art. 140", titulo),
                Paragraph(f"<b>{leyenda}</b>", ParagraphStyle("ley", parent=normal, alignment=2))]],
              colWidths=[140 * mm, 40 * mm],
              style=[("VALIGN", (0, 0), (-1, -1), "MIDDLE")]),
        Spacer(1, 2 * mm),
    ]

    # a) Empleador
    elementos.append(Table(
        [[Paragraph(f"<b>Empleador:</b> {d.empresa_razon_social}", normal),
          Paragraph(f"<b>CUIT:</b> {d.empresa_cuit}", normal)],
         [Paragraph(f"<b>Domicilio:</b> {d.empresa_domicilio}", normal), ""]],
        colWidths=[120 * mm, 60 * mm],
        style=[("BOX", (0, 0), (-1, -1), 0.5, grilla), ("SPAN", (0, 1), (1, 1)),
               ("BACKGROUND", (0, 0), (-1, -1), gris)]))
    elementos.append(Spacer(1, 2 * mm))

    # b) y k) Trabajador, categoría, CUIL, fecha de ingreso
    elementos.append(Table(
        [[Paragraph(f"<b>Trabajador:</b> {d.empleado_apellido}, {d.empleado_nombre}", normal),
          Paragraph(f"<b>CUIL:</b> {d.empleado_cuil}", normal)],
         [Paragraph(f"<b>Categoría:</b> {liq.categoria} ({d.convenio})", normal),
          Paragraph(f"<b>Fecha de ingreso:</b> {_f(d.empleado_fecha_ingreso)}", normal)],
         *_filas_periodo(liq, normal),
         [Paragraph(f"<b>Legajo:</b> {d.empleado_legajo or '-'}", normal),
          Paragraph(f"<b>Jornada:</b> {liq.jornada_horas} hs diarias", normal)]],
        colWidths=[120 * mm, 60 * mm],
        style=[("BOX", (0, 0), (-1, -1), 0.5, grilla)]))
    elementos.append(Spacer(1, 3 * mm))

    # c) e) f) Conceptos
    columnas = ("remunerativo", "no_remunerativo", "descuento")
    filas = [["Cód.", "Concepto", "Determinación", "Remun.", "No remun.", "Descuentos"]]
    for c in liq.conceptos:
        tipo = "no_remunerativo" if c.tipo == "indemnizacion" else c.tipo
        importes = [pesos(c.importe) if tipo == t else "" for t in columnas]
        desc = c.descripcion + (" <i>(sin aportes)</i>" if c.tipo == "indemnizacion" else "")
        filas.append([c.codigo, Paragraph(desc, chico), Paragraph(c.detalle, chico), *importes])
    filas.append(["", Paragraph("<b>Totales</b>", chico), Paragraph("Bruto remunerativo / no remunerativo / descuentos", chico),
                  pesos(liq.total_remunerativo), pesos(liq.total_no_remunerativo), pesos(liq.total_descuentos)])
    tabla = Table(filas, colWidths=[16 * mm, 42 * mm, 56 * mm, 22 * mm, 22 * mm, 22 * mm], repeatRows=1)
    tabla.setStyle(TableStyle([
        ("FONT", (0, 0), (-1, 0), "Helvetica-Bold", 8),
        ("FONT", (0, 1), (-1, -1), "Helvetica", 8),
        ("FONT", (3, -1), (-1, -1), "Helvetica-Bold", 8),
        ("BACKGROUND", (0, 0), (-1, 0), gris),
        ("BACKGROUND", (0, -1), (-1, -1), gris),
        ("ALIGN", (3, 0), (-1, -1), "RIGHT"),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("GRID", (0, 0), (-1, -1), 0.4, grilla),
    ]))
    elementos.append(tabla)
    elementos.append(Spacer(1, 3 * mm))

    # g) Neto en números y letras
    elementos.append(Table(
        [[Paragraph("<b>NETO A COBRAR</b>", normal), Paragraph(f"<b>$ {pesos(liq.neto)}</b>",
                                                                ParagraphStyle("neto", parent=normal, alignment=2, fontSize=11))],
         [Paragraph(f"<b>Son:</b> {numero_a_letras(liq.neto)}", normal), ""]],
        colWidths=[130 * mm, 50 * mm],
        style=[("BOX", (0, 0), (-1, -1), 0.8, colors.black), ("SPAN", (0, 1), (1, 1)),
               ("BACKGROUND", (0, 0), (-1, 0), gris)]))
    elementos.append(Spacer(1, 3 * mm))

    # d) Último depósito de aportes, i) lugar y fecha de pago
    elementos.append(Table(
        [[Paragraph(f"<b>Último depósito de aportes:</b> período {d.ultimo_deposito_periodo}, "
                    f"fecha {_f(d.ultimo_deposito_fecha)}, banco {d.ultimo_deposito_banco}", chico)],
         [Paragraph(f"<b>Lugar y fecha de pago:</b> {d.lugar_pago}, {_f(d.fecha_pago)}", chico)]],
        colWidths=[180 * mm],
        style=[("BOX", (0, 0), (-1, -1), 0.5, grilla)]))
    elementos.append(Spacer(1, 14 * mm))

    # h) Constancia de recepción del duplicado
    if leyenda == "DUPLICADO":
        firma = ("Recibí el importe neto de esta liquidación y el duplicado de este recibo.",
                 "Firma del trabajador")
    else:
        firma = ("El presente es copia fiel del duplicado firmado por el trabajador.",
                 "Firma y sello del empleador")
    elementos.append(Table(
        [[Paragraph(firma[0], chico), "_______________________________"],
         ["", Paragraph(firma[1], ParagraphStyle("f", parent=chico, alignment=1))]],
        colWidths=[110 * mm, 70 * mm],
        style=[("VALIGN", (0, 0), (-1, -1), "BOTTOM"), ("ALIGN", (1, 0), (1, -1), "CENTER")]))
    return elementos


def generar_recibos(destino, lista: list) -> None:
    """Escribe un PDF con original + duplicado de cada recibo de `lista`."""
    estilos = getSampleStyleSheet()
    primero = lista[0]
    titulo = (f"Recibo {primero.empleado_apellido} {primero.liquidacion.periodo}" if len(lista) == 1
              else f"Recibos {primero.empresa_razon_social} {primero.liquidacion.periodo}")
    doc = SimpleDocTemplate(destino, pagesize=A4, leftMargin=15 * mm, rightMargin=15 * mm,
                            topMargin=15 * mm, bottomMargin=15 * mm,
                            title=titulo, author=primero.empresa_razon_social)
    historia = []
    for datos in lista:
        if historia:
            historia.append(PageBreak())
        historia += _copia(datos, "ORIGINAL", estilos) + [PageBreak()] + _copia(datos, "DUPLICADO", estilos)
    doc.build(historia)


def generar_recibo(destino, datos: DatosRecibo) -> None:
    """Escribe el PDF (original + duplicado) de un recibo."""
    generar_recibos(destino, [datos])
