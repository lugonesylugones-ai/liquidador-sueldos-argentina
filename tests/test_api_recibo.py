from io import BytesIO

from openpyxl import Workbook
from pypdf import PdfReader

from backend.escalas import COLUMNAS
from tests.conftest import subir

LIQ = {
    "periodo": "2026-09", "fecha_pago": "2026-10-03", "lugar_pago": "CABA",
    "ultimo_deposito_periodo": "08/2026", "ultimo_deposito_fecha": "2026-09-10",
    "ultimo_deposito_banco": "Banco Nación",
}


def test_liquidacion_usa_escala_vigente(client, empleado):
    # Agrego una vigencia posterior: septiembre tiene que seguir con la de julio.
    wb = Workbook()
    ws = wb.active
    ws.title = "Escala"
    ws.append(COLUMNAS)
    ws.append(["Vendedor A", 9999999, "01/10/2026"])
    buf = BytesIO()
    wb.save(buf)
    subir(client, buf.getvalue())

    r = client.post("/liquidaciones", json={"empleado_id": empleado, **LIQ})
    assert r.status_code == 201, r.json
    assert r.json["basico_escala"] == "1170000.00"      # Vendedor A en el fixture
    assert r.json["vigencia_escala"] == "2026-07-01"
    assert r.json["anios_antiguedad"] == 6

    r = client.post("/liquidaciones", json={"empleado_id": empleado, **LIQ, "periodo": "2026-10"})
    assert r.json["basico_escala"] == "9999999.00"


def test_sin_escala_vigente_da_error_claro(client, empleado):
    r = client.post("/liquidaciones", json={"empleado_id": empleado, **LIQ, "periodo": "2026-06"})
    assert r.status_code == 400
    assert "No hay escala" in r.json["error"]


def test_faltan_datos_del_recibo(client, empleado):
    datos = {k: v for k, v in LIQ.items() if k != "ultimo_deposito_banco"}
    r = client.post("/liquidaciones", json={"empleado_id": empleado, **datos})
    assert r.status_code == 400
    assert "ultimo_deposito_banco" in r.json["error"]


def test_reliquidar_mismo_periodo_reemplaza(client, empleado):
    a = client.post("/liquidaciones", json={"empleado_id": empleado, **LIQ}).json
    b = client.post("/liquidaciones", json={"empleado_id": empleado, **LIQ,
                                            "inasistencias_injustificadas": 1}).json
    assert a["id"] == b["id"]
    assert b["neto"] != a["neto"]


def test_recibo_pdf_cumple_art_140(client, empleado):
    liq = client.post("/liquidaciones", json={"empleado_id": empleado, **LIQ}).json
    r = client.get(f"/liquidaciones/{liq['id']}/recibo.pdf")
    assert r.status_code == 200
    assert r.mimetype == "application/pdf"
    pdf = PdfReader(BytesIO(r.data))
    assert len(pdf.pages) == 2
    original, duplicado = (p.extract_text() for p in pdf.pages)
    for texto in (original, duplicado):
        # a) empleador
        assert "Test SA" in texto and "30-11111111-1" in texto and "Calle 1" in texto
        # b) y k) trabajador, CUIL, categoría, ingreso
        assert "Gómez, Juan" in texto and "20-22222222-2" in texto
        assert "Vendedor A" in texto and "30/09/2020" in texto
        # c) determinación de cada concepto
        assert "6 años × 1% s/ $ 1.170.000,00" in texto and "8,33% s/ $ 1.240.200,00" in texto
        # d) último depósito
        assert "08/2026" in texto and "10/09/2026" in texto and "Banco Nación" in texto
        # e) f) totales y aportes
        assert "Jubilación 11%" in texto and "FAECYS" in texto and "No remun." in texto
        # g) neto en números y letras
        assert "NETO A COBRAR" in texto and "Son: Pesos" in texto
        # i) lugar y fecha de pago
        assert "CABA, 03/10/2026" in texto
    assert "ORIGINAL" in original
    # h) constancia de recepción del duplicado
    assert "DUPLICADO" in duplicado and "duplicado de este recibo" in duplicado


def test_recibo_inexistente(client):
    assert client.get("/liquidaciones/999/recibo.pdf").status_code == 404


def test_jornada_parcial_y_no_remunerativo_de_punta_a_punta(client):
    wb = Workbook()
    ws = wb.active
    ws.title = "Escala"
    ws.append(COLUMNAS)
    ws.append(["Auxiliar B", 1209365, "01/09/2026", 120000])
    buf = BytesIO()
    wb.save(buf)
    assert subir(client, buf.getvalue()).status_code == 201
    empresa = client.post("/empresas", json={
        "razon_social": "Comercio Test", "cuit": "30-99999999-9", "domicilio": "Calle 2"}).json["id"]
    emp = client.post("/empleados", json={
        "empresa_id": empresa, "apellido": "Parcial", "nombre": "Ana", "cuil": "27-33333333-3",
        "categoria": "Auxiliar B", "fecha_ingreso": "2004-12-20", "jornada_horas": 4}).json["id"]
    r = client.post("/liquidaciones", json={"empleado_id": emp, **LIQ})
    assert r.status_code == 201, r.json
    assert r.json["neto"] == "651388.00"                 # igual al recibo real de sep/2026
    assert r.json["jornada_horas"] == 4


def test_jornada_invalida(client, escala_cargada):
    empresa = client.post("/empresas", json={
        "razon_social": "X", "cuit": "30-1", "domicilio": "Y"}).json["id"]
    r = client.post("/empleados", json={
        "empresa_id": empresa, "apellido": "A", "nombre": "B", "cuil": "20-1",
        "categoria": "Auxiliar B", "fecha_ingreso": "2020-01-01", "jornada_horas": 10})
    assert r.status_code == 400
