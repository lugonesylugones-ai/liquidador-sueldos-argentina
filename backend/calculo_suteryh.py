"""Motor de cálculo para trabajadores de edificios (CCT 589/10, FATERYH / SUTERYH).

Ninguna regla está validada contra un recibo real todavía. Las que dependen de
una interpretación están marcadas "VERIFICAR" y se pueden cambiar con un
parámetro. La fuente de cada regla está en la especificación del hilo SUTERYH.

- Básico según cargo y categoría del edificio (art. 6: 1ra a 4ta según servicios centrales).
- Adicional remuneratorio mensual de la planilla; 50% en cargos de media jornada (VERIFICAR).
- Antigüedad: monto fijo por año. 2% del básico de ayudante permanente sin vivienda
  de 4ta; 1% para media jornada, suplentes y jornalizados (art. 11).
- Valor vivienda: remunerativo (art. 15). Se imprime como haber y se descuenta igual
  importe porque se paga en especie (VERIFICAR).
- Plus por tareas, retiro de residuos por UF, título de encargado integral y zona desfavorable.
- Horas extra al 50% y al 100%: valor hora = remuneración habitual / 200 (VERIFICAR divisor).
- Aportes sobre el remunerativo: jubilación 11%, Ley 19.032 3%, obra social 3%,
  Caja de Protección a la Familia 1% (art. 19), FMVDD 1% (art. 27), seguro de vida
  0,75% (art. 27 bis) y cuota sindical 2% solo a afiliados (VERIFICAR alícuota local).
- El neto no se redondea (VERIFICAR).
- Mes incompleto: básico, adicional y vivienda × días / 30.

Las contribuciones del empleador propias del convenio quedan en
`Liquidacion.informativos`: van en las boletas sindicales, no en el recibo.
"""
from datetime import date
from decimal import Decimal

from .calculo import (Concepto, Liquidacion, anios_cumplidos, liquidar_sac, redondear, ultimo_dia,
                      _pct)
from .formato import pesos

CONVENIO_SUTERYH = "CCT 589/10"
DIAS_MES = Decimal("30")
DIVISOR_HORA = Decimal("200")
CATEGORIAS_EDIFICIO = (1, 2, 3, 4)

# (clave, nombre, con vivienda, media jornada). El nombre es la categoría del empleado.
CARGOS = [
    ("encargado_permanente_cv", "Encargado Permanente con vivienda", True, False),
    ("encargado_permanente_sv", "Encargado Permanente sin vivienda", False, False),
    ("ayudante_permanente_cv", "Ayudante Permanente con vivienda", True, False),
    ("ayudante_permanente_sv", "Ayudante Permanente sin vivienda", False, False),
    ("ayudante_media_jornada", "Ayudante Media jornada", False, True),
    ("personal_asimilado_cv", "Personal Asimilado con vivienda", True, False),
    ("personal_asimilado_sv", "Personal Asimilado sin vivienda", False, False),
    ("mayordomo_cv", "Mayordomo con vivienda", True, False),
    ("mayordomo_sv", "Mayordomo sin vivienda", False, False),
    ("intendente", "Intendente", False, False),
    ("personal_mas_una_funcion_cv", "Personal con más de 1 función con vivienda", True, False),
    ("personal_mas_una_funcion_sv", "Personal con más de 1 función sin vivienda", False, False),
    ("encargado_guardacoches_cv", "Encargado Guardacoches con vivienda", True, False),
    ("encargado_guardacoches_sv", "Encargado Guardacoches sin vivienda", False, False),
    ("vigilancia_nocturna", "Personal Vigilancia Nocturna", False, False),
    ("vigilancia_diurna", "Personal Vigilancia Diurna", False, False),
    ("vigilancia_media_jornada", "Personal Vigilancia Media Jornada", False, True),
    ("encargado_no_permanente_cv", "Encargado No Permanente con vivienda", True, False),
    ("encargado_no_permanente_sv", "Encargado No Permanente sin vivienda", False, False),
    ("ayudante_temporario", "Ayudante Temporario", False, False),
    ("ayudante_temporario_media_jornada", "Ayudante Temporario Media Jornada", False, True),
]
CARGO_POR_NOMBRE = {nombre: (clave, viv, media) for clave, nombre, viv, media in CARGOS}
CATEGORIAS_SUTERYH = [nombre for _, nombre, _, _ in CARGOS]

# Cargos que cobran la antigüedad al 1% (art. 11: media jornada, suplentes y jornalizados).
CARGOS_ANTIGUEDAD_1PCT = {"ayudante_media_jornada", "vigilancia_media_jornada",
                          "ayudante_temporario_media_jornada"}

# Montos de la planilla que no dependen del cargo. Clave -> nombre en la escala.
ADICIONALES = {
    "adicional_remuneratorio_mensual": "Adicional remuneratorio mensual",
    "plus_antiguedad_2pct": "Antigüedad por año (2%)",
    "plus_antiguedad_1pct": "Antigüedad por año (1%)",
    "valor_vivienda": "Valor vivienda",
    "retiro_residuos_por_uf": "Retiro de residuos por UF",
    "clasificacion_residuos": "Clasificación de residuos",
    "jardin": "Plus jardín",
    "limpieza_cocheras": "Plus limpieza cocheras",
    "movimiento_coches": "Plus movimiento coches",
    "limpieza_piletas": "Plus limpieza piletas",
    "viaticos": "Adicional viáticos",
    "titulo_encargado_integral_pct": "Título encargado integral (%)",
    "zona_desfavorable_pct": "Zona desfavorable (%)",
}

# Tareas que se pagan como importe fijo: clave -> código del recibo.
TAREAS = {
    "clasificacion_residuos": "CLAS",
    "jardin": "JARD",
    "limpieza_cocheras": "COCH",
    "movimiento_coches": "MOVC",
    "limpieza_piletas": "PILE",
    "viaticos": "VIAT",
}

# (código, descripción, alícuota, solo afiliados, admite tope de base imponible)
APORTES = [
    ("JUB", "Jubilación 11% (Ley 24.241)", Decimal("0.11"), False, True),
    ("PAMI", "Ley 19.032 INSSJP 3%", Decimal("0.03"), False, True),
    ("OS", "Obra social 3%", Decimal("0.03"), False, False),
    ("CPF", "Caja Protección Familia art. 19 CCT 589/10", Decimal("0.01"), False, False),
    ("FMVDD", "FMVDD art. 27 CCT 589/10", Decimal("0.01"), False, False),
    ("A27B", "Seguro de vida art. 27 bis CCT 589/10", Decimal("0.0075"), False, False),
    ("SIND", "Cuota sindical", Decimal("0.02"), True, False),
]

# Contribuciones del empleador del convenio (boletas sindicales). SERACARH: fuente de 2019.
CONTRIBUCIONES_CONVENIO = [
    ("C_CPF", "Caja Protección Familia art. 19", Decimal("0.015")),
    ("C_FMVDD", "FMVDD art. 27", Decimal("0.04")),
    ("C_A27B", "Seguro de vida art. 27 bis", Decimal("0.0075")),
    ("C_SERACARH", "SERACARH (VERIFICAR vigencia)", Decimal("0.005")),
]


def fila_basico(cargo: str, categoria_edificio: int | str) -> str:
    """Nombre de la fila de escala para un cargo y categoría de edificio: 'Intendente|2'."""
    return f"{cargo}|{categoria_edificio}"


def nombres_escala() -> list:
    """Todas las filas que puede tener una escala SUTERYH, en el orden de la plantilla."""
    return [fila_basico(n, c) for n in CATEGORIAS_SUTERYH for c in CATEGORIAS_EDIFICIO] + list(ADICIONALES.values())


def _agregar(liq: Liquidacion, codigo, descripcion, detalle, tipo, importe: Decimal) -> Decimal:
    importe = redondear(importe)
    if importe:
        liq.conceptos.append(Concepto(codigo, descripcion, detalle, tipo, importe))
    return importe


def _suma(liq: Liquidacion, tipo: str) -> Decimal:
    return sum((c.importe for c in liq.de_tipo(tipo)), Decimal("0"))


def liquidar_suteryh(
    *,
    periodo: str,
    cargo: str,
    categoria_edificio: int,
    basico: Decimal,
    adicionales: dict,
    vigencia_escala: date,
    fecha_ingreso: date,
    dias: int = 30,
    unidades_funcionales: int = 0,
    tareas=(),
    tramos_titulo: int = 0,
    zona_desfavorable: bool = False,
    horas_50: Decimal | int = 0,
    horas_100: Decimal | int = 0,
    afiliado: bool = True,
    alicuota_sindical: Decimal | None = None,
    vivienda_en_especie: bool = True,
    factor_adicional: Decimal | None = None,
    tope_base_imponible: Decimal | None = None,
) -> Liquidacion:
    """Un mes de un trabajador de edificio.

    `cargo`: nombre del cargo (ver CARGOS). `basico`: el de la escala para el cargo y
    la categoría del edificio. `adicionales`: {clave de ADICIONALES: monto} vigentes.
    `tramos_titulo`: tramos del título de encargado integral, 5% cada uno (art. 28;
    la planilla dice 10%: VERIFICAR). `factor_adicional`: proporción del adicional
    remuneratorio; por defecto 0,5 en media jornada y 1 en el resto (VERIFICAR).
    """
    if cargo not in CARGO_POR_NOMBRE:
        raise ValueError(f"cargo desconocido: {cargo}")
    if categoria_edificio not in CATEGORIAS_EDIFICIO:
        raise ValueError("la categoría del edificio tiene que ser 1, 2, 3 o 4")
    if not 1 <= dias <= 30:
        raise ValueError("los días a liquidar tienen que estar entre 1 y 30")
    if horas_50 < 0 or horas_100 < 0 or unidades_funcionales < 0 or tramos_titulo < 0:
        raise ValueError("horas extra, unidades funcionales y tramos no pueden ser negativos")
    desconocidas = [t for t in tareas if t not in TAREAS]
    if desconocidas:
        raise ValueError(f"tarea desconocida: {', '.join(desconocidas)}")
    clave, con_vivienda, media_jornada = CARGO_POR_NOMBRE[cargo]
    clave_ant = "plus_antiguedad_1pct" if clave in CARGOS_ANTIGUEDAD_1PCT else "plus_antiguedad_2pct"
    anios = anios_cumplidos(fecha_ingreso, ultimo_dia(periodo))
    necesarios = {"adicional_remuneratorio_mensual"}
    if anios:
        necesarios.add(clave_ant)
    if con_vivienda:
        necesarios.add("valor_vivienda")
    if unidades_funcionales:
        necesarios.add("retiro_residuos_por_uf")
    if tramos_titulo:
        necesarios.add("titulo_encargado_integral_pct")
    if zona_desfavorable:
        necesarios.add("zona_desfavorable_pct")
    faltan = sorted(ADICIONALES[k] for k in necesarios | set(tareas) if k not in adicionales)
    if faltan:
        raise ValueError(f"falta en la escala: {', '.join(faltan)}")
    adic = {k: Decimal(v) for k, v in adicionales.items()}

    liq = Liquidacion(
        periodo=periodo, categoria=f"{cargo} · {categoria_edificio}ª categoría", basico_escala=basico,
        no_remunerativo_escala=Decimal("0"), vigencia_escala=vigencia_escala,
        jornada_horas=4 if media_jornada else 8, anios_antiguedad=anios, dias_trabajados=dias)
    prop = Decimal(dias) / DIAS_MES
    dias_txt = "" if dias == 30 else f" × {dias}/30 días"

    basico_mes = _agregar(liq, "BAS", "Sueldo básico" if dias == 30 else "Sueldo proporcional",
                          f"{cargo}, {categoria_edificio}ª categoría{dias_txt}", "remunerativo", basico * prop)

    if factor_adicional is None:
        factor_adicional = Decimal("0.5") if media_jornada else Decimal("1")
    monto = adic["adicional_remuneratorio_mensual"]
    factor_txt = "" if factor_adicional == 1 else f" × {_pct(Decimal(factor_adicional))}"
    _agregar(liq, "ADR", "Adicional remuneratorio CCT", f"$ {pesos(monto)}{factor_txt}{dias_txt}",
             "remunerativo", monto * Decimal(factor_adicional) * prop)

    if anios:
        _agregar(liq, "ANT", "Antigüedad", f"{anios} {'año' if anios == 1 else 'años'} × $ {pesos(adic[clave_ant])}",
                 "remunerativo", adic[clave_ant] * anios)

    if con_vivienda:
        _agregar(liq, "VIV", "Valor vivienda", f"Art. 15 CCT 589/10{dias_txt}", "remunerativo",
                 adic["valor_vivienda"] * prop)

    if unidades_funcionales:
        _agregar(liq, "RES", "Retiro de residuos",
                 f"{unidades_funcionales} UF × $ {pesos(adic['retiro_residuos_por_uf'])}", "remunerativo",
                 adic["retiro_residuos_por_uf"] * unidades_funcionales)

    for tarea in tareas:
        _agregar(liq, TAREAS[tarea], ADICIONALES[tarea], "Planilla salarial", "remunerativo", adic[tarea])

    if tramos_titulo:
        pct = adic["titulo_encargado_integral_pct"] * tramos_titulo / 2 / 100
        _agregar(liq, "TIT", "Título encargado integral", f"{_pct(pct)} s/ $ {pesos(basico_mes)}",
                 "remunerativo", basico_mes * pct)

    if zona_desfavorable:
        base = _suma(liq, "remunerativo")
        pct = adic["zona_desfavorable_pct"] / 100
        _agregar(liq, "ZONA", "Plus zona desfavorable", f"{_pct(pct)} s/ $ {pesos(base)}", "remunerativo",
                 base * pct)

    if horas_50 or horas_100:
        habitual = _suma(liq, "remunerativo")
        valor_hora = habitual / DIVISOR_HORA
        hora_txt = f"$ {pesos(redondear(valor_hora))} (habitual / 200)"
        _agregar(liq, "HE50", "Horas extra 50%", f"{horas_50} hs × {hora_txt} × 1,5", "remunerativo",
                 valor_hora * Decimal(horas_50) * Decimal("1.5"))
        _agregar(liq, "HE100", "Horas extra 100%", f"{horas_100} hs × {hora_txt} × 2", "remunerativo",
                 valor_hora * Decimal(horas_100) * 2)

    aportes_y_neto(liq, afiliado=afiliado, alicuota_sindical=alicuota_sindical,
                   vivienda_en_especie=vivienda_en_especie, tope_base_imponible=tope_base_imponible)
    return liq


def aportes_y_neto(liq: Liquidacion, *, afiliado: bool = True, alicuota_sindical: Decimal | None = None,
                   vivienda_en_especie: bool = True, tope_base_imponible: Decimal | None = None) -> None:
    """Aportes del convenio, vivienda en especie, totales y contribuciones informativas."""
    rem = _suma(liq, "remunerativo")
    no_rem = _suma(liq, "no_remunerativo")
    for codigo, desc, alicuota, solo_afiliados, con_tope in APORTES:
        if solo_afiliados and not afiliado:
            continue
        if codigo == "SIND" and alicuota_sindical is not None:
            alicuota = Decimal(alicuota_sindical)
        base, detalle = rem, f"{_pct(alicuota)} s/ $ {pesos(rem)}"
        if con_tope and tope_base_imponible is not None and rem > tope_base_imponible:
            base, detalle = tope_base_imponible, f"{_pct(alicuota)} s/ tope $ {pesos(tope_base_imponible)}"
        _agregar(liq, codigo, f"{desc} {_pct(alicuota)}" if codigo == "SIND" else desc, detalle,
                 "descuento", base * alicuota)

    vivienda = sum((c.importe for c in liq.remunerativos() if c.codigo == "VIV"), Decimal("0"))
    if vivienda and vivienda_en_especie:
        _agregar(liq, "VIVE", "Vivienda (en especie)", "Compensa el haber de vivienda", "descuento", vivienda)

    liq.total_remunerativo = rem
    liq.total_no_remunerativo = no_rem
    liq.total_descuentos = _suma(liq, "descuento")
    liq.neto = rem + no_rem - liq.total_descuentos
    liq.informativos = [Concepto(c, desc, f"{_pct(a)} s/ $ {pesos(rem)}", "contribucion", redondear(rem * a))
                        for c, desc, a in CONTRIBUCIONES_CONVENIO]


def liquidar_sac_suteryh(*, afiliado: bool = True, alicuota_sindical: Decimal | None = None,
                         tope_base_imponible: Decimal | None = None, **datos) -> Liquidacion:
    """SAC con el régimen general (`liquidar_sac`) y los aportes del convenio.

    La base incluye la vivienda (art. 15), pero el SAC se paga en efectivo: no se
    descuenta vivienda en especie. Que los aportes del convenio (CPF, FMVDD, seguro)
    se apliquen sobre el SAC es una inferencia: VERIFICAR.
    """
    return liquidar_sac(**datos, aportes=lambda liq: aportes_y_neto(
        liq, afiliado=afiliado, alicuota_sindical=alicuota_sindical, vivienda_en_especie=False,
        tope_base_imponible=tope_base_imponible))
