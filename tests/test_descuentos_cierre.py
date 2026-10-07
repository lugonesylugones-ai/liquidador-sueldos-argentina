"""Descuentos varios (mutual, embargo, préstamo, anticipo), cierre de períodos y copias de seguridad."""
import re
from decimal import Decimal
from pathlib import Path

import pytest

from backend.app import create_app

PAGO = {"fecha_pago": "2026-10-05", "lugar_pago": "Bahía Blanca", "ultimo_deposito_periodo": "08/2026",
        "ultimo_deposito_fecha": "2026-09-10", "ultimo_deposito_banco": "Banco Nación"}


@pytest.fixture
def app(tmp_path):
    return create_app({"DATABASE": str(tmp_path / "d.db"), "TESTING": True})


@pytest.fixture
def client(app):
    return app.test_client()


def _empresa(client) -> int:
    return client.post("/api/empresas", json={"razon_social": "Uno SRL", "cuit": "30-71234567-1",
                                              "domicilio": "X 1", "lugar_pago": "Bahía Blanca"}).json["id"]


def _empleado(client, e, **extra) -> int:
    r = client.post("/api/empleados", json={
        "empresa_id": e, "legajo": "1", "apellido": "Pérez", "nombre": "Ana", "cuil": "27-33333333-9",
        "categoria": "Auxiliar B", "fecha_ingreso": "2015-03-01", **extra})
    assert r.status_code == 201, r.json
    return r.json["id"]


def _liquidar(client, emp_id, **extra):
    return client.post("/api/liquidaciones", json={**PAGO, "empleado_id": emp_id, "periodo": "2026-09", **extra})


def _conceptos(liq) -> dict:
    return {c["codigo"]: Decimal(c["importe"]) for c in liq["conceptos"]}


def test_mutual_prestamo_y_anticipo_antes_del_redondeo(client):
    e = _empresa(client)
    sin = _liquidar(client, _empleado(client, e)).json
    emp = _empleado(client, e, descuentos=[
        {"tipo": "mutual", "importe": "26631.34"},
        {"tipo": "prestamo", "detalle": "cuota 3/6", "importe": "50000", "hasta": "2026-09"},
        {"tipo": "otro", "importe": "1000", "hasta": "2026-08"}])        # ya terminó: no se descuenta
    liq = _liquidar(client, emp, anticipo="100000").json
    c = _conceptos(liq)
    assert c["DMUT"] == Decimal("26631.34") and c["DPRE"] == Decimal("50000") and c["DANT"] == Decimal("100000")
    assert "DOTR" not in c
    # Mismos haberes y aportes; el neto baja exactamente los descuentos (y se vuelve a redondear).
    assert liq["total_remunerativo"] == sin["total_remunerativo"]
    sin_red = Decimal(sin["neto"]) - _conceptos(sin).get("RED", 0)
    con_red = Decimal(liq["neto"]) - c.get("RED", 0)
    assert sin_red - con_red == Decimal("176631.34")
    assert Decimal(liq["neto"]) == Decimal(liq["neto"]).to_integral_value()
    descripcion = next(x["descripcion"] for x in liq["conceptos"] if x["codigo"] == "DPRE")
    assert descripcion == "Cuota préstamo cuota 3/6"
    # En octubre el préstamo ya no está.
    octubre = client.post("/api/liquidaciones", json={**PAGO, "empleado_id": emp, "periodo": "2026-10"})
    if octubre.status_code == 201:
        assert "DPRE" not in _conceptos(octubre.json)


def test_embargo_con_minimo_y_recibo_de_zona_fria(client):
    """Como el recibo real: 20% del bruto menos el mínimo en el sueldo, y 20% de todo en la zona fría."""
    r = client.post("/empresas/nueva", data={"razon_social": "Consorcio X", "cuit": "30712345671",
                                             "domicilio": "Calle 1", "lugar_pago": "Bahía Blanca"})
    e = int(r.headers["Location"].rstrip("/").split("/")[-1])
    client.post(f"/empresas/{e}/edificio", data={"categoria": "3", "unidades_funcionales": "20",
                                                 "zona_desfavorable": "1", "zona_recibo_aparte": "1"})
    r = client.post(f"/empresas/{e}/empleados", data={
        "apellido": "Paz", "nombre": "Juan", "cuil": "20-22222222-3", "convenio": "CCT 589/10",
        "categoria": "Personal Vigilancia Nocturna", "fecha_ingreso": "2017-10-01", "afiliado": "1",
        "descuento_tipo_1": "embargo", "descuento_detalle_1": "Oficio 1/26", "descuento_porcentaje_1": "20",
        "descuento_minimo_1": "383800"}, follow_redirects=True)
    assert "guardado" in r.text
    emp = client.get(f"/api/empresas/{e}/empleados").json[0]
    assert emp["descuentos"] == [{"tipo": "embargo", "detalle": "Oficio 1/26", "hasta": "",
                                  "porcentaje": "20", "minimo": "383800"}]
    assert 'value="383800"' in client.get(f"/empresas/{e}?editar={emp['id']}").text

    r = client.post(f"/empresas/{e}/liquidar", data={**PAGO, "tipo": "mensual", "periodo": "2026-09"},
                    follow_redirects=True)
    assert "Listo: 1 recibos" in r.text
    filas = client.get(f"/empresas/{e}/liquidaciones/2026-09").text
    liq_id = re.search(r'href="/liquidaciones/(\d+)"', filas).group(1)
    pantalla = client.get(f"/liquidaciones/{liq_id}").text
    assert "Embargo judicial Oficio 1/26" in pantalla

    from backend.db import connect
    import json
    conn = connect(client.application.config["DATABASE"])
    for tipo, minimo in (("mensual", Decimal("383800")), ("zona_fria", Decimal("0"))):
        liq = json.loads(conn.execute("SELECT resultado FROM liquidaciones WHERE tipo = ?",
                                      (tipo,)).fetchone()[0])["liquidacion"]
        c = _conceptos(liq)
        bruto = Decimal(liq["total_remunerativo"]) + Decimal(liq["total_no_remunerativo"]) - c.get("RED", 0)
        assert c["DEMB"] == ((bruto - minimo) * Decimal("0.2")).quantize(Decimal("0.01"))

    # El archivo para ARCA lleva el embargo como "otros descuentos".
    txt = client.get(f"/api/empresas/{e}/arca/2026-09.txt?tipo=mensual").data.decode()
    # (con el código del contador de los consorcios para el embargo)
    assert any(l[:2] == "03" and l[13:23].strip() == "04060" and l[44] == "D" for l in txt.split("\r\n"))
    assert "04060" in client.get(f"/api/empresas/{e}/arca/conceptos.txt").data.decode("cp1252")


@pytest.mark.parametrize("descuento, error", [
    ({"tipo": "cuota"}, "Tipo de descuento desconocido"),
    ({"tipo": "mutual"}, "un importe fijo o un porcentaje"),
    ({"tipo": "mutual", "importe": "10", "porcentaje": "5"}, "un importe fijo o un porcentaje"),
    ({"tipo": "embargo", "porcentaje": "150"}, "entre 0 y 100"),
    ({"tipo": "prestamo", "importe": "10", "hasta": "octubre"}, "AAAA-MM"),
])
def test_descuentos_invalidos(client, descuento, error):
    r = client.post("/api/empleados", json={
        "empresa_id": _empresa(client), "apellido": "Pérez", "nombre": "Ana", "cuil": "27-33333333-9",
        "categoria": "Auxiliar B", "fecha_ingreso": "2015-03-01", "descuentos": [descuento]})
    assert r.status_code == 400 and error in r.json["error"]


def test_descuentos_que_superan_el_sueldo(client):
    e = _empresa(client)
    emp = _empleado(client, e, descuentos=[{"tipo": "otro", "importe": "99999999"}])
    r = _liquidar(client, emp)
    assert r.status_code == 400 and "superan" in r.json["error"]


def test_periodo_cerrado_no_se_vuelve_a_liquidar(client):
    e = _empresa(client)
    emp = _empleado(client, e)
    assert _liquidar(client, emp).status_code == 201
    r = client.post(f"/empresas/{e}/periodos/2026-09", data={"accion": "cerrar"}, follow_redirects=True)
    assert "cerrado" in r.text and "Reabrir" in r.text and "Cerrado" in r.text

    for respuesta in (_liquidar(client, emp),
                      client.post(f"/api/empresas/{e}/liquidaciones", json={**PAGO, "periodo": "2026-09"}),
                      client.post("/api/liquidaciones/final", json={**PAGO, "empleado_id": emp,
                                                                    "fecha_egreso": "2026-09-15",
                                                                    "causa": "renuncia"})):
        assert respuesta.status_code == 400 and "está cerrado" in respuesta.json["error"]
    # Cerrado se sigue pudiendo ver y bajar (recibos, ARCA), y los otros meses se liquidan.
    assert client.get(f"/api/empresas/{e}/recibos/2026-09.pdf").status_code == 200
    assert client.get(f"/api/empresas/{e}/arca/2026-09.txt").status_code == 200
    assert client.post("/api/liquidaciones", json={**PAGO, "empleado_id": emp,
                                                   "periodo": "2026-08"}).status_code == 201

    client.post(f"/empresas/{e}/periodos/2026-09", data={"accion": "reabrir"})
    assert _liquidar(client, emp).status_code == 201


def test_copia_de_seguridad_diaria_y_manual(app, client, tmp_path):
    copias = list((tmp_path / "copias").glob("d_*.db"))
    assert len(copias) == 1          # al arrancar
    client.get("/")
    assert len(list((tmp_path / "copias").glob("d_*.db"))) == 1   # una por día
    r = client.get("/copia-de-seguridad")
    assert r.status_code == 200 and r.data[:15] == b"SQLite format 3"
    assert "Bajar copia ahora" in client.get("/").text


def test_conserva_las_ultimas_copias(tmp_path):
    from backend.db import copia_de_seguridad
    base = tmp_path / "x.db"
    import sqlite3
    sqlite3.connect(base).close()
    carpeta = tmp_path / "c"
    carpeta.mkdir()
    for dia in range(1, 6):
        (carpeta / f"x_2026-01-0{dia}.db").write_bytes(b"")
    copia_de_seguridad(str(base), str(carpeta), conservar=3)
    quedan = sorted(p.name for p in Path(carpeta).glob("x_*.db"))
    assert len(quedan) == 3 and quedan[-1].startswith("x_20") and "x_2026-01-01.db" not in quedan
