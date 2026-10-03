"""Pantallas web: todo el flujo desde el navegador.

Pensado para usar en la propia compu (sin usuarios ni login). Los formularios
hacen POST y vuelven con un mensaje; las descargas usan las rutas de /api.
"""
import json
from datetime import date
from decimal import Decimal

from flask import Blueprint, abort, flash, redirect, render_template, request, url_for

from . import servicios as sv
from .calculo import CAUSAS_EGRESO, primer_dia
from .db import get_db
from .escalas import CONVENIO_COMERCIO

bp = Blueprint("web", __name__)


def _avisar_error(exc: sv.ErrorDatos) -> None:
    flash(str(exc), "error")
    for linea in exc.detalle:
        flash(str(linea), "detalle")


def _empresa(empresa_id: int):
    empresa = sv.empresa(get_db(), empresa_id)
    if empresa is None:
        abort(404)
    return empresa


def _mes_siguiente(periodo: str) -> str:
    anio, mes = (int(p) for p in periodo.split("-"))
    return f"{anio + mes // 12}-{mes % 12 + 1:02d}"


def _archivo():
    archivo = request.files.get("archivo")
    if archivo is None or not archivo.filename:
        raise sv.ErrorDatos("Elegí un archivo .xlsx")
    if not archivo.filename.lower().endswith(".xlsx"):
        raise sv.ErrorDatos("Solo se aceptan archivos .xlsx")
    return archivo


@bp.get("/")
def inicio():
    conn = get_db()
    return render_template("inicio.html", empresas=sv.empresas(conn), resumen=sv.resumen_general(conn),
                           escalas=sv.escalas(conn)[:1])


# --- Empresas ------------------------------------------------------------------
@bp.get("/empresas")
def empresas():
    return render_template("empresas.html", empresas=sv.empresas(get_db()))


@bp.post("/empresas/nueva")
def crear_empresa():
    try:
        empresa_id = sv.crear_empresa(get_db(), request.form)
    except sv.ErrorDatos as exc:
        _avisar_error(exc)
        return redirect(url_for("web.empresas"))
    flash("Empresa creada. Ahora cargá sus empleados.", "ok")
    return redirect(url_for("web.empresa", empresa_id=empresa_id))


@bp.get("/empresas/<int:empresa_id>")
def empresa(empresa_id: int):
    conn = get_db()
    editar = request.args.get("editar", type=int)
    empleado = None
    if editar:
        empleado = next((e for e in sv.empleados(conn, empresa_id) if e["id"] == editar), None)
    return render_template(
        "empresa.html", empresa=_empresa(empresa_id), empleados=sv.empleados(conn, empresa_id),
        empleado=empleado, categorias=sv.categorias(conn),
        liquidaciones=sv.liquidaciones_por_periodo(conn, empresa_id),
        periodo_sugerido=_periodo_sugerido(conn, empresa_id))


def _periodo_sugerido(conn, empresa_id: int) -> str:
    mensuales = [r["periodo"] for r in sv.liquidaciones_por_periodo(conn, empresa_id) if r["tipo"] == "mensual"]
    return _mes_siguiente(max(mensuales)) if mensuales else date.today().strftime("%Y-%m")


@bp.post("/empresas/<int:empresa_id>/empleados")
def guardar_empleado(empresa_id: int):
    _empresa(empresa_id)
    try:
        sv.guardar_empleado(get_db(), empresa_id, request.form)
    except sv.ErrorDatos as exc:
        _avisar_error(exc)
        return redirect(url_for("web.empresa", empresa_id=empresa_id, editar=request.form.get("id") or None))
    flash(f"Empleado {request.form.get('apellido')}, {request.form.get('nombre')} guardado.", "ok")
    return redirect(url_for("web.empresa", empresa_id=empresa_id))


@bp.post("/empresas/<int:empresa_id>/empleados/importar")
def importar_empleados(empresa_id: int):
    _empresa(empresa_id)
    try:
        n = sv.importar_empleados(get_db(), empresa_id, _archivo().read())
    except sv.ErrorDatos as exc:
        _avisar_error(exc)
    else:
        flash(f"Se cargaron o actualizaron {n} empleados.", "ok")
    return redirect(url_for("web.empresa", empresa_id=empresa_id))


# --- Liquidar ------------------------------------------------------------------
@bp.get("/empresas/<int:empresa_id>/liquidar")
def liquidar_form(empresa_id: int):
    empresa = _empresa(empresa_id)
    conn = get_db()
    tipo = request.args.get("tipo", "mensual")
    if tipo not in sv.FUNCIONES:
        abort(404)
    try:
        periodo = sv.periodo(request.args)
    except sv.ErrorDatos as exc:
        _avisar_error(exc)
        return redirect(url_for("web.empresa", empresa_id=empresa_id))
    previo = sv.ultimo_pago(conn, empresa_id)
    return render_template(
        "liquidar.html", empresa=empresa, tipo=tipo, periodo=periodo,
        empleados=sv.empleados_del_periodo(conn, empresa_id, tipo, periodo),
        ya_liquidado=bool(sv.liquidaciones_de(conn, empresa_id, periodo, tipo)),
        datos={"lugar_pago": empresa["lugar_pago"] or "", **previo})


@bp.post("/empresas/<int:empresa_id>/liquidar")
def liquidar(empresa_id: int):
    _empresa(empresa_id)
    form = request.form
    tipo = form.get("tipo", "mensual")
    if tipo not in sv.FUNCIONES:
        abort(400)
    por_empleado = {}
    for clave, valor in form.items():
        if clave.startswith("inasistencias_") and valor.strip():
            por_empleado[int(clave.split("_", 1)[1])] = {"inasistencias_injustificadas": valor}
    try:
        periodo, hechas, errores = sv.liquidar_empresa(get_db(), empresa_id, tipo, form, por_empleado)
    except sv.ErrorDatos as exc:
        _avisar_error(exc)
        return redirect(url_for("web.liquidar_form", empresa_id=empresa_id, tipo=tipo,
                                periodo=form.get("periodo") or None))
    if hechas:
        flash(f"Listo: {len(hechas)} recibos de {_titulo(tipo, periodo)}.", "ok")
    for e in errores:
        flash(f"{e['empleado']}: {e['error']}", "error")
    if not hechas:
        return redirect(url_for("web.liquidar_form", empresa_id=empresa_id, tipo=tipo, periodo=periodo))
    return redirect(url_for("web.liquidaciones", empresa_id=empresa_id, periodo=periodo, tipo=tipo))


TITULOS = {"mensual": "Sueldos", "sac": "Aguinaldo", "final": "Liquidaciones finales"}


def _titulo(tipo: str, periodo: str) -> str:
    return f"aguinaldo {periodo}" if tipo == "sac" else periodo


# --- Liquidación final -----------------------------------------------------------
def _empleado(empresa_id: int, empleado_id: int):
    emp = get_db().execute("SELECT * FROM empleados WHERE id = ? AND empresa_id = ?",
                           (empleado_id, empresa_id)).fetchone()
    if emp is None:
        abort(404)
    return emp


@bp.get("/empresas/<int:empresa_id>/empleados/<int:empleado_id>/final")
def final_form(empresa_id: int, empleado_id: int):
    empresa = _empresa(empresa_id)
    emp = _empleado(empresa_id, empleado_id)
    conn = get_db()
    previa = conn.execute("SELECT id, periodo, resultado FROM liquidaciones WHERE empleado_id = ? AND tipo = 'final'",
                          (empleado_id,)).fetchone()
    datos = {"lugar_pago": empresa["lugar_pago"] or "", **sv.ultimo_pago(conn, empresa_id),
             "fecha_egreso": emp["fecha_egreso"] or "", "causa": "", "preaviso_otorgado": False}
    if previa:
        datos.update(json.loads(previa["resultado"])["liquidacion"].get("egreso") or {})
    return render_template("final.html", empresa=empresa, emp=emp, causas=CAUSAS_EGRESO,
                           previa=previa, datos=datos)


@bp.post("/empresas/<int:empresa_id>/empleados/<int:empleado_id>/final")
def final(empresa_id: int, empleado_id: int):
    _empresa(empresa_id)
    _empleado(empresa_id, empleado_id)
    try:
        liq_id, liq = sv.liquidar_egreso(get_db(), empleado_id, request.form)
    except sv.ErrorDatos as exc:
        _avisar_error(exc)
        return redirect(url_for("web.final_form", empresa_id=empresa_id, empleado_id=empleado_id))
    flash(f"Liquidación final lista: neto $ {liq.neto:,.2f}".replace(",", "X").replace(".", ",").replace("X", "."), "ok")
    return redirect(url_for("web.liquidacion", liq_id=liq_id))


@bp.get("/liquidaciones/<int:liq_id>")
def liquidacion(liq_id: int):
    datos = sv.liquidacion(get_db(), liq_id)
    if datos is None:
        abort(404)
    return render_template("liquidacion.html", d=datos, liq=datos["liq"], empresa=_empresa(datos["empresa_id"]),
                           causas=CAUSAS_EGRESO, titulos=TITULOS)


@bp.get("/empresas/<int:empresa_id>/liquidaciones/<periodo>")
def liquidaciones(empresa_id: int, periodo: str):
    empresa = _empresa(empresa_id)
    tipo = request.args.get("tipo", "mensual")
    try:
        primer_dia(periodo)
    except ValueError:
        abort(404)
    filas = sv.liquidaciones_de(get_db(), empresa_id, periodo, tipo)
    total = sum((Decimal(f["neto"]) for f in filas), Decimal("0"))
    return render_template("liquidaciones.html", empresa=empresa, periodo=periodo, tipo=tipo,
                           filas=filas, total=total, titulos=TITULOS)


# --- Escalas y convenios ------------------------------------------------------------
@bp.get("/escalas")
def escalas():
    conn = get_db()
    convenio = request.args.get("convenio", CONVENIO_COMERCIO)
    if convenio not in sv.categorias(conn):
        abort(404)
    por_vigencia = {}
    for f in sv.escalas(conn, convenio):
        por_vigencia.setdefault(f["vigencia_desde"], []).append(f)
    return render_template("escalas.html", por_vigencia=por_vigencia, convenio=convenio,
                           convenios=sv.convenios(conn))


@bp.post("/escalas/importar")
def importar_escala():
    convenio = request.form.get("convenio") or CONVENIO_COMERCIO
    try:
        archivo = _archivo()
        n = sv.importar_escala(get_db(), archivo.read(), archivo.filename, convenio)
    except sv.ErrorDatos as exc:
        _avisar_error(exc)
    else:
        flash(f"Se cargaron {n} filas de escala.", "ok")
    return redirect(url_for("web.escalas", convenio=convenio))


def _lineas(texto: str) -> list:
    return [l.strip() for l in (texto or "").splitlines() if l.strip()]


@bp.get("/convenios")
def convenios():
    return render_template("convenios.html", convenios=sv.convenios(get_db()))


@bp.post("/convenios/nuevo")
def crear_convenio():
    try:
        codigo = sv.crear_convenio(get_db(), {"codigo": request.form.get("codigo"),
                                              "nombre": request.form.get("nombre"),
                                              "categorias": _lineas(request.form.get("categorias"))})
    except sv.ErrorDatos as exc:
        _avisar_error(exc)
    else:
        flash(f"Convenio {codigo} creado. Ahora cargá su escala.", "ok")
    return redirect(url_for("web.convenios"))


@bp.post("/convenios/categorias")
def agregar_categorias():
    codigo = request.form.get("codigo", "")
    try:
        n = sv.agregar_categorias(get_db(), codigo, _lineas(request.form.get("categorias")))
    except sv.ErrorDatos as exc:
        _avisar_error(exc)
    else:
        flash(f"Se agregaron {n} categorías a {codigo}.", "ok")
    return redirect(url_for("web.convenios"))
