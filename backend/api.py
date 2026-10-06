"""API JSON del liquidador, bajo /api. La lógica vive en `servicios`."""
from io import BytesIO

from flask import Blueprint, abort, jsonify, request, send_file

from . import servicios as sv
from .db import get_db
from .empleados import generar_plantilla_empleados
from .escalas import CONVENIO_COMERCIO, generar_plantilla

XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"

bp = Blueprint("api", __name__, url_prefix="/api")


@bp.errorhandler(sv.ErrorDatos)
def _error_datos(exc: sv.ErrorDatos):
    if exc.detalle:
        return jsonify(error=str(exc), detalle=exc.detalle), 422
    return jsonify(error=str(exc)), 400


def archivo_xlsx():
    archivo = request.files.get("archivo")
    if archivo is None or not archivo.filename:
        raise sv.ErrorDatos("Subí el Excel en el campo 'archivo'")
    if not archivo.filename.lower().endswith(".xlsx"):
        raise sv.ErrorDatos("Solo se aceptan archivos .xlsx")
    return archivo


def _empresa_o_404(empresa_id: int):
    if sv.empresa(get_db(), empresa_id) is None:
        abort(404)


def _pdf(resultado):
    if resultado is None:
        abort(404)
    contenido, nombre = resultado
    return send_file(BytesIO(contenido), mimetype="application/pdf", download_name=nombre)


# --- Convenios y escalas -------------------------------------------------------
@bp.get("/convenios")
def listar_convenios():
    return jsonify(sv.convenios(get_db()))


@bp.post("/convenios")
def crear_convenio():
    """Alta de otro convenio con sus categorías (sin motor de cálculo todavía)."""
    d = request.get_json(force=True)
    codigo = sv.crear_convenio(get_db(), d)
    return jsonify(codigo=codigo, categorias=len(d.get("categorias") or [])), 201


@bp.get("/escalas/plantilla")
def descargar_plantilla():
    convenio = request.args.get("convenio", CONVENIO_COMERCIO)
    cats = sv.filas_de_escala(get_db(), convenio)
    nombre = "comercio" if convenio == CONVENIO_COMERCIO else "".join(c if c.isalnum() else "_" for c in convenio)
    return send_file(BytesIO(generar_plantilla(categorias=cats)), as_attachment=True,
                     download_name=f"plantilla_escala_{nombre}.xlsx", mimetype=XLSX)


@bp.post("/escalas/importar")
def importar_escala():
    archivo = archivo_xlsx()
    convenio = request.form.get("convenio") or CONVENIO_COMERCIO
    n = sv.importar_escala(get_db(), archivo.read(), archivo.filename, convenio)
    return jsonify(importadas=n, convenio=convenio), 201


@bp.get("/escalas")
def listar_escalas():
    return jsonify(sv.escalas(get_db(), request.args.get("convenio", CONVENIO_COMERCIO)))


# --- Empresas y empleados ------------------------------------------------------
@bp.post("/empresas")
def crear_empresa():
    conn = get_db()
    empresa_id = sv.crear_empresa(conn, request.get_json(force=True))
    return jsonify(id=empresa_id, cuit=sv.empresa(conn, empresa_id)["cuit"]), 201


@bp.get("/empresas")
def listar_empresas():
    return jsonify(sv.empresas(get_db()))


@bp.get("/empresas/<int:empresa_id>/empleados")
def listar_empleados(empresa_id: int):
    _empresa_o_404(empresa_id)
    return jsonify(sv.empleados(get_db(), empresa_id))


@bp.get("/empresas/<int:empresa_id>/edificio")
def ver_edificio(empresa_id: int):
    _empresa_o_404(empresa_id)
    return jsonify(sv.edificio(get_db(), empresa_id))


@bp.put("/empresas/<int:empresa_id>/edificio")
def guardar_edificio(empresa_id: int):
    """Consorcios (CCT 589/10): categoría del edificio, unidades funcionales y zona desfavorable."""
    _empresa_o_404(empresa_id)
    conn = get_db()
    sv.guardar_edificio(conn, empresa_id, request.get_json(force=True))
    return jsonify(sv.edificio(conn, empresa_id))


@bp.post("/empleados")
def crear_empleado():
    d = request.get_json(force=True)
    return jsonify(id=sv.guardar_empleado(get_db(), sv.requerido(d, "empresa_id"), d)), 201


@bp.get("/empleados/plantilla")
def plantilla_empleados():
    contenido = generar_plantilla_empleados(sv.categorias(get_db()))
    return send_file(BytesIO(contenido), as_attachment=True,
                     download_name="plantilla_empleados.xlsx", mimetype=XLSX)


@bp.post("/empresas/<int:empresa_id>/empleados/importar")
def importar_empleados(empresa_id: int):
    _empresa_o_404(empresa_id)
    return jsonify(importados=sv.importar_empleados(get_db(), empresa_id, archivo_xlsx().read())), 201


# --- Liquidaciones y recibos ---------------------------------------------------
def _liquidar_uno(tipo: str):
    liq_id, liq = sv.liquidar_uno(get_db(), tipo, request.get_json(force=True))
    return jsonify(id=liq_id, **liq.to_dict()), 201


@bp.post("/liquidaciones")
def crear_liquidacion():
    return _liquidar_uno("mensual")


@bp.post("/liquidaciones/sac")
def crear_sac():
    return _liquidar_uno("sac")


@bp.post("/liquidaciones/final")
def crear_final():
    """Liquidación final: empleado_id, fecha_egreso, causa y los datos de pago."""
    d = request.get_json(force=True)
    liq_id, liq = sv.liquidar_egreso(get_db(), sv.requerido(d, "empleado_id"), d)
    return jsonify(id=liq_id, **liq.to_dict()), 201


def _liquidar_empresa(empresa_id: int, tipo: str):
    _empresa_o_404(empresa_id)
    periodo, hechas, errores = sv.liquidar_empresa(get_db(), empresa_id, tipo, request.get_json(force=True))
    return jsonify(periodo=periodo, liquidaciones=hechas, errores=errores), (201 if hechas else 400)


@bp.post("/empresas/<int:empresa_id>/liquidaciones")
def liquidar_empresa(empresa_id: int):
    """Liquida el mes a todos los empleados activos de la empresa."""
    return _liquidar_empresa(empresa_id, "mensual")


@bp.post("/empresas/<int:empresa_id>/sac")
def sac_empresa(empresa_id: int):
    """Liquida el SAC a todos los que trabajaron en el semestre."""
    return _liquidar_empresa(empresa_id, "sac")


@bp.get("/liquidaciones/<int:liq_id>/recibo.pdf")
def recibo(liq_id: int):
    return _pdf(sv.pdf_recibo(get_db(), liq_id))


@bp.get("/empresas/<int:empresa_id>/recibos/<periodo>.pdf")
def recibos_empresa(empresa_id: int, periodo: str):
    """Todos los recibos de la empresa para el período, en un solo PDF (?tipo=sac para el SAC)."""
    return _pdf(sv.pdf_recibos_empresa(get_db(), empresa_id, periodo, request.args.get("tipo", "mensual")))


# --- ARCA: Libro de Sueldos Digital / F.931 -------------------------------------
def _txt(contenido: str, nombre: str):
    # ARCA pide el archivo en ANSI (Windows-1252).
    return send_file(BytesIO(contenido.encode("cp1252", errors="replace")), mimetype="text/plain",
                     as_attachment=True, download_name=nombre)


@bp.get("/empresas/<int:empresa_id>/arca/<periodo>.txt")
def archivo_arca(empresa_id: int, periodo: str):
    """Liquidación del período para importar en el Libro de Sueldos Digital (arma el F.931)."""
    _empresa_o_404(empresa_id)
    return _txt(*sv.archivo_arca(get_db(), empresa_id, periodo))


@bp.get("/empresas/<int:empresa_id>/arca/conceptos.txt")
def conceptos_arca(empresa_id: int):
    """Relación de los conceptos del liquidador con los de ARCA, para parametrizarlos una vez."""
    _empresa_o_404(empresa_id)
    return _txt(*sv.archivo_conceptos_arca(get_db(), empresa_id))


@bp.get("/empresas/<int:empresa_id>/arca")
def ver_datos_arca(empresa_id: int):
    _empresa_o_404(empresa_id)
    return jsonify(sv.datos_arca(get_db(), empresa_id))


@bp.put("/empresas/<int:empresa_id>/arca")
def guardar_datos_arca(empresa_id: int):
    _empresa_o_404(empresa_id)
    conn = get_db()
    sv.guardar_datos_arca(conn, empresa_id, request.get_json(force=True))
    return jsonify(sv.datos_arca(conn, empresa_id))


# --- Conceptos propios ---------------------------------------------------------
@bp.get("/conceptos")
def listar_conceptos():
    return jsonify(sv.conceptos_propios(get_db()))


@bp.post("/conceptos")
def guardar_concepto():
    """Alta o modificación (por código) de un concepto propio y su fórmula."""
    conn = get_db()
    codigo = sv.guardar_concepto(conn, request.get_json(force=True))
    return jsonify(sv.concepto_propio(conn, codigo)), 201


@bp.get("/conceptos/<codigo>")
def ver_concepto(codigo: str):
    c = sv.concepto_propio(get_db(), codigo)
    if c is None:
        abort(404)
    return jsonify(c)
