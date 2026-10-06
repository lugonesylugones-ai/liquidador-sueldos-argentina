"""Conceptos propios: el usuario los crea, dice cómo se calculan y el liquidador los respeta
en aportes, SAC, vacaciones y el Libro de Sueldos Digital."""
import json
from datetime import date
from decimal import Decimal as D

import pytest

from backend import conceptos as cp
from backend.app import create_app
from backend.db import connect
from backend.calculo import liquidar_comercio, liquidar_final, liquidar_sac, redondear

PAGO = {"fecha_pago": "2026-10-05", "lugar_pago": "Bahía Blanca", "ultimo_deposito_periodo": "08/2026",
        "ultimo_deposito_fecha": "2026-09-10", "ultimo_deposito_banco": "Banco Nación"}
ESCALA = dict(categoria="Auxiliar B", basico=D("1209365"), no_remunerativo=D("120000"),
              vigencia_escala=date(2026, 9, 1), fecha_ingreso=date(2017, 7, 3))


@pytest.fixture
def client(tmp_path):
    return create_app({"DATABASE": str(tmp_path / "c.db"), "TESTING": True}).test_client()


def _empresa(client) -> int:
    return client.post("/api/empresas", json={"razon_social": "Uno SRL", "cuit": "30-71234567-1",
                                              "domicilio": "X 1", "lugar_pago": "Bahía Blanca"}).json["id"]


def _empleado(client, e, cuil="27-33333333-9", **extra) -> int:
    r = client.post("/api/empleados", json={
        "empresa_id": e, "legajo": cuil[-3:], "apellido": "Pérez", "nombre": "Ana", "cuil": cuil,
        "categoria": "Auxiliar B", "fecha_ingreso": "2017-07-03", **extra})
    assert r.status_code == 201, r.json
    return r.json["id"]


def _concepto(client, **d):
    r = client.post("/api/conceptos", json=d)
    assert r.status_code == 201, r.json
    return r.json


def _liquidar(client, emp_id, periodo="2026-09", **extra):
    r = client.post("/api/liquidaciones", json={**PAGO, "empleado_id": emp_id, "periodo": periodo, **extra})
    assert r.status_code == 201, r.json
    return r.json


def _c(liq) -> dict:
    return {c["codigo"]: D(c["importe"]) for c in liq["conceptos"]}


def _defs(*defs):
    return lambda liq, dias: cp.aplicar_haberes(liq, list(defs), dias=dias)


# --- Motor -------------------------------------------------------------------------
def test_sin_conceptos_propios_el_recibo_no_cambia():
    sin = liquidar_comercio(periodo="2026-09", **ESCALA)
    con = liquidar_comercio(periodo="2026-09", **ESCALA, conceptos_extra=_defs())
    assert sin.to_dict() == con.to_dict()
    assert "habitual" not in sin.to_dict()["conceptos"][0]   # el JSON guardado no cambia


def test_plus_remunerativo_fijo_paga_todos_los_aportes():
    sin = liquidar_comercio(periodo="2026-09", **ESCALA)
    plus = cp.Definicion("PLUSV", "Plus vidriera", "remunerativo", "fijo", valor=D("100000"))
    con = liquidar_comercio(periodo="2026-09", **ESCALA, conceptos_extra=_defs(plus))
    c, s = ({x.codigo: x.importe for x in l.conceptos} for l in (con, sin))
    assert c["PLUSV"] == D("100000")
    assert con.total_remunerativo - sin.total_remunerativo == D("100000")
    assert c["JUB"] - s["JUB"] == D("11000") and c["PAMI"] - s["PAMI"] == D("3000")
    assert c["OS"] - s["OS"] == D("3000") and c["A100"] - s["A100"] == D("2000")


def test_suma_fija_proporcional_a_la_jornada():
    plus = cp.Definicion("PLUSV", "Plus vidriera", "remunerativo", "fijo", valor=D("100000"))
    liq = liquidar_comercio(periodo="2026-09", **{**ESCALA, "jornada_horas": 4}, conceptos_extra=_defs(plus))
    c = next(x for x in liq.conceptos if x.codigo == "PLUSV")
    assert c.importe == D("50000") and c.detalle == "$ 100.000,00 × 4/8 hs"
    fijo = cp.Definicion("PLUSV", "Plus", "remunerativo", "fijo", valor=D("100000"), proporcional=False)
    liq = liquidar_comercio(periodo="2026-09", **{**ESCALA, "jornada_horas": 4}, conceptos_extra=_defs(fijo))
    assert next(x for x in liq.conceptos if x.codigo == "PLUSV").importe == D("100000")


def test_no_remunerativo_con_y_sin_obra_social():
    sin = {x.codigo: x.importe for x in liquidar_comercio(periodo="2026-09", **ESCALA).conceptos}
    con_os = cp.Definicion("BONO", "Bono", "no_remunerativo", "fijo", valor=D("100000"))
    sin_os = cp.Definicion("BONO", "Bono", "no_remunerativo", "fijo", valor=D("100000"), aportes=False)
    a = {x.codigo: x.importe for x in liquidar_comercio(periodo="2026-09", **ESCALA,
                                                         conceptos_extra=_defs(con_os)).conceptos}
    b = {x.codigo: x.importe for x in liquidar_comercio(periodo="2026-09", **ESCALA,
                                                         conceptos_extra=_defs(sin_os)).conceptos}
    assert a["JUB"] == b["JUB"] == sin["JUB"]                      # no remunerativo: sin jubilación
    assert a["OS"] - sin["OS"] == D("3000") and a["FAECYS"] - sin["FAECYS"] == D("500")
    assert b["OS"] == sin["OS"] and b["A101"] == sin["A101"]


def test_porcentaje_de_otros_conceptos_y_formula():
    pct = cp.Definicion("PCAJ", "Plus cajero", "remunerativo", "porcentaje", valor=D("10"), base=("BAS", "ANT"))
    form = cp.Definicion("GUAR", "Guardias", "remunerativo", "formula",
                         formula="(BAS + ANT) / 200 * 1.5 * CANTIDAD", cantidad=D("8"))
    liq = liquidar_comercio(periodo="2026-09", **ESCALA, conceptos_extra=_defs(pct, form))
    c = {x.codigo: x for x in liq.conceptos}
    base = c["BAS"].importe + c["ANT"].importe
    assert c["PCAJ"].importe == redondear(base / 10)
    assert c["PCAJ"].detalle == "10% s/ $ 1.318.207,85"
    assert c["GUAR"].importe == redondear(base / 200 * D("1.5") * 8)
    # Un concepto puede usar a los propios que van antes.
    sobre = cp.Definicion("SOBRE", "Sobre plus", "remunerativo", "porcentaje", valor=D("50"), base=("PCAJ",))
    liq = liquidar_comercio(periodo="2026-09", **ESCALA, conceptos_extra=_defs(pct, sobre))
    c = {x.codigo: x.importe for x in liq.conceptos}
    assert c["SOBRE"] == redondear(c["PCAJ"] / 2)


def test_formulas_no_ejecutan_codigo():
    for mala in ("__import__('os')", "BAS.real", "[1]", "BAS if 1 else 2", "open('x')", "1 ** 2"):
        with pytest.raises(cp.ErrorFormula):
            cp.evaluar(mala, {})
    assert cp.evaluar("max(BAS - 10, 0) / 2", {"BAS": D("30")}) == D("10")
    assert cp.evaluar("X * 2", {}) == 0
    with pytest.raises(cp.ErrorFormula):
        cp.evaluar("BAS / 0", {"BAS": D("1")})


def test_sac_no_cuenta_lo_no_habitual():
    habitual = cp.Definicion("BONO", "Bono", "no_remunerativo", "fijo", valor=D("100000"))
    premio = cp.Definicion("PREM", "Premio único", "no_remunerativo", "fijo", valor=D("100000"), habitual=False)
    meses = [liquidar_comercio(periodo=p, **ESCALA, conceptos_extra=_defs(habitual, premio))
             for p in ("2026-07", "2026-08", "2026-09")]
    base = [liquidar_comercio(periodo=p, **ESCALA) for p in ("2026-07", "2026-08", "2026-09")]
    sac = {c.codigo: c.importe for c in liquidar_sac(periodo="2026-12", categoria="Auxiliar B",
                                                       fecha_ingreso=ESCALA["fecha_ingreso"], historial=meses).conceptos}
    sac0 = {c.codigo: c.importe for c in liquidar_sac(periodo="2026-12", categoria="Auxiliar B",
                                                        fecha_ingreso=ESCALA["fecha_ingreso"], historial=base).conceptos}
    assert sac["SACNR"] - sac0["SACNR"] == D("50000")              # el bono habitual sí, el premio no
    assert sac["SAC"] == sac0["SAC"]


def test_final_vacaciones_con_plus_habitual():
    plus = cp.Definicion("PLUSV", "Plus vidriera", "remunerativo", "fijo", valor=D("250000"))
    premio = cp.Definicion("PREM", "Premio", "remunerativo", "fijo", valor=D("250000"), habitual=False)
    datos = dict(categoria="Auxiliar B", basico=D("1209365"), no_remunerativo=D("120000"),
                 vigencia_escala=date(2026, 9, 1), fecha_ingreso=date(2017, 7, 3),
                 fecha_egreso=date(2026, 9, 30), causa="renuncia", historial=[])
    sin = {c.codigo: c.importe for c in liquidar_final(**datos).conceptos}
    con = {c.codigo: c.importe for c in liquidar_final(**datos, conceptos_extra=_defs(plus, premio)).conceptos}
    dias_vac = D("15.71")   # 21 × 273/365 redondeado, sin gozadas
    assert con["VAC"] - sin["VAC"] == redondear(D("250000") / 25 * dias_vac)


# --- Alta, asignación y liquidación -------------------------------------------------
def test_validaciones_al_crear(client):
    base = {"descripcion": "X", "tipo": "remunerativo", "calculo": "fijo", "valor": "1", "arca": "160000"}
    for d, error in [({"codigo": "BAS"}, "ya lo usa"), ({"codigo": "INASX"}, "ya lo usa"),
                     ({"codigo": "x"}, "mayúsculas"), ({"codigo": "PLUS", "arca": "16"}, "6 dígitos"),
                     ({"codigo": "PLUS", "calculo": "porcentaje"}, "sobre qué"),
                     ({"codigo": "PLUS", "calculo": "porcentaje", "base": "BAS, NOEXISTE"}, "NOEXISTE"),
                     ({"codigo": "PLUS", "calculo": "formula", "formula": "PLUS * 2"}, "sí mismo"),
                     ({"codigo": "PLUS", "calculo": "formula", "formula": "__import__('os')"}, "funciones")]:
        r = client.post("/api/conceptos", json={**base, **d})
        assert r.status_code == 400 and error in r.json["error"], (d, r.json)


def test_concepto_automatico_y_asignado_desde_la_api(client):
    e = _empresa(client)
    sin = _liquidar(client, _empleado(client, e))
    _concepto(client, codigo="PLUSV", descripcion="Plus vidriera", tipo="remunerativo", calculo="fijo",
              valor="100000", arca="160000", convenio="CCT 130/75", automatico=True)
    _concepto(client, codigo="GUAR", descripcion="Guardias", tipo="remunerativo", calculo="cantidad",
              valor="5000", arca="130000")
    _concepto(client, codigo="SEGV", descripcion="Seguro de vida optativo", tipo="descuento",
              calculo="porcentaje", valor="1", base="TOTAL_REM", arca="820000")
    emp = _empleado(client, e, cuil="20-22222222-3",
                    conceptos=[{"codigo": "GUAR", "cantidad": "4"}, {"codigo": "SEGV"}])
    otro = _empleado(client, e, cuil="20-11111111-2")
    liq = _liquidar(client, emp, cantidades={"GUAR": "6"})            # la cantidad del mes pisa la fija
    c = _c(liq)
    assert c["PLUSV"] == D("100000") and c["GUAR"] == D("30000")
    assert D(liq["total_remunerativo"]) - D(sin["total_remunerativo"]) == D("130000")
    assert c["SEGV"] == redondear(D(liq["total_remunerativo"]) / 100)
    c_otro = _c(_liquidar(client, otro))
    assert "PLUSV" in c_otro and "GUAR" not in c_otro and "SEGV" not in c_otro
    # Desactivado deja de liquidarse.
    _concepto(client, codigo="PLUSV", descripcion="Plus vidriera", tipo="remunerativo", calculo="fijo",
              valor="100000", arca="160000", automatico=True, activo=False)
    assert "PLUSV" not in _c(_liquidar(client, otro))


def test_asignar_un_concepto_que_no_existe(client):
    e = _empresa(client)
    r = client.post("/api/empleados", json={
        "empresa_id": e, "apellido": "A", "nombre": "B", "cuil": "27-33333333-9", "categoria": "Auxiliar B",
        "fecha_ingreso": "2017-07-03", "conceptos": [{"codigo": "NOPE"}]})
    assert r.status_code == 400 and "NOPE" in r.json["error"]


def test_libro_de_sueldos_con_conceptos_propios(client):
    e = _empresa(client)
    _concepto(client, codigo="PLUSV", descripcion="Plus vidriera", tipo="remunerativo", calculo="fijo",
              valor="100000", arca="160000", automatico=True)
    _concepto(client, codigo="BONO", descripcion="Bono sin aportes", tipo="no_remunerativo", calculo="fijo",
              valor="50000", arca="540000", automatico=True, aportes=False)
    _liquidar(client, _empleado(client, e))
    lineas = client.get(f"/api/empresas/{e}/arca/conceptos.txt").data.decode("cp1252").split("\r\n")[:-1]
    plus = next(l for l in lineas if l[6:16].strip() == "PLUSV")
    bono = next(l for l in lineas if l[6:16].strip() == "BONO")
    bas = next(l for l in lineas if l[6:16].strip() == "BAS")
    assert plus[:6] == "160000" and plus[167:186] == bas[167:186]
    assert bono[:6] == "540000" and bono[167:186] == "0" * 19
    texto = client.get(f"/api/empresas/{e}/arca/2026-09.txt").data.decode("cp1252")
    regs03 = [l for l in texto.split("\r\n") if l[:2] == "03"]
    assert any(l[13:23].strip() == "PLUSV" for l in regs03) and any(l[13:23].strip() == "BONO" for l in regs03)


def test_encargado_con_plus_propio_entra_en_zona_y_aportes():
    from tests.test_suteryh import liquidar
    plus = cp.Definicion("PLUSP", "Plus pileta climatizada", "remunerativo", "fijo", valor=D("40000"))
    bono = cp.Definicion("BONO", "Bono", "no_remunerativo", "fijo", valor=D("10000"))
    datos = ("2026-09", "encargado_permanente_sv", 2, 5)
    sin = {c.codigo: c.importe for c in liquidar(*datos, zona_desfavorable=True).conceptos}
    con = {c.codigo: c.importe for c in liquidar(*datos, zona_desfavorable=True,
                                                 conceptos_extra=_defs(plus, bono)).conceptos}
    assert con["ZONA"] - sin["ZONA"] == D("20000")                # 50% del plus
    # Aportes sobre plus + su zona (60.000); el bono no remunerativo no paga nada en edificios.
    assert con["JUB"] - sin["JUB"] == D("6600") and con["OS"] - sin["OS"] == D("1800")


# --- Pantallas ---------------------------------------------------------------------
def test_flujo_desde_la_web(client):
    from werkzeug.datastructures import MultiDict
    assert "Nuevo concepto" in client.get("/conceptos").text
    # Checkbox tildado + su hidden "0": gana el tildado; destildado, el "0".
    form = MultiDict([("codigo", "guar"), ("descripcion", "Guardias"), ("tipo", "remunerativo"),
                      ("calculo", "cantidad"), ("valor", "5000"), ("arca", "130000"), ("orden", "100"),
                      ("habitual", "1"), ("proporcional", "0"), ("habitual", "0"), ("aportes", "0"),
                      ("automatico", "0"), ("activo", "1")])
    r = client.post("/conceptos", data=form, follow_redirects=True)
    assert "Concepto GUAR guardado" in r.text and "× cantidad" in r.text
    c = client.get("/api/conceptos/GUAR").json
    assert c["habitual"] == 1 and c["proporcional"] == 0 and c["valor"] == "5000"
    assert "Editar GUAR" in client.get("/conceptos?editar=GUAR").text
    r = client.post("/conceptos", data={"codigo": "BAS", "descripcion": "x", "tipo": "remunerativo",
                                        "calculo": "fijo", "arca": "160000"}, follow_redirects=True)
    assert "ya lo usa" in r.text

    e = _empresa(client)
    r = client.post(f"/empresas/{e}/empleados", follow_redirects=True, data={
        "apellido": "Pérez", "nombre": "Ana", "cuil": "27-33333333-9", "convenio": "CCT 130/75",
        "categoria": "Auxiliar B", "fecha_ingreso": "2017-07-03", "jornada_horas": "8",
        "concepto_codigo_1": "GUAR", "concepto_cantidad_1": "2"})
    assert "guardado" in r.text
    emp = client.get(f"/api/empresas/{e}/empleados").json[0]
    assert emp["conceptos"] == [{"codigo": "GUAR", "cantidad": "2"}]
    assert 'name="concepto_codigo_1"' in client.get(f"/empresas/{e}?editar={emp['id']}").text

    pantalla = client.get(f"/empresas/{e}/liquidar?periodo=2026-09&tipo=mensual").text
    assert f'name="cantidad_GUAR_{emp["id"]}"' in pantalla
    r = client.post(f"/empresas/{e}/liquidar", data={**PAGO, "tipo": "mensual", "periodo": "2026-09",
                                                     f"cantidad_GUAR_{emp['id']}": "3"})
    assert r.status_code == 302
    conn = connect(client.application.config["DATABASE"])
    resultado = json.loads(conn.execute("SELECT resultado FROM liquidaciones").fetchone()[0])
    conn.close()
    guar = next(x for x in resultado["liquidacion"]["conceptos"] if x["codigo"] == "GUAR")
    assert D(guar["importe"]) == D("15000") and guar["detalle"] == "3 × $ 5.000,00"
