"""Varias empresas, alta e importación de empleados, liquidación por empresa y SAC."""
from io import BytesIO

import pytest
from openpyxl import load_workbook
from pypdf import PdfReader

from backend.app import create_app
from backend.cuit import normalizar_cuit

PAGO = {"fecha_pago": "2026-10-03", "lugar_pago": "CABA", "ultimo_deposito_periodo": "08/2026",
        "ultimo_deposito_fecha": "2026-09-10", "ultimo_deposito_banco": "Banco Nación"}


@pytest.fixture
def client(tmp_path):
    # Con las escalas iniciales cargadas, como una instalación nueva.
    return create_app({"DATABASE": str(tmp_path / "t.db"), "TESTING": True}).test_client()


def planilla(client, filas):
    wb = load_workbook(BytesIO(client.get("/api/empleados/plantilla").data))
    for f in filas:
        wb["Empleados"].append(f)
    buf = BytesIO()
    wb.save(buf)
    return buf.getvalue()


def importar(client, empresa, contenido):
    return client.post(f"/api/empresas/{empresa}/empleados/importar",
                       data={"archivo": (BytesIO(contenido), "empleados.xlsx")},
                       content_type="multipart/form-data")


def empresa(client, cuit="30-71234567-1", razon="Uno SRL"):
    r = client.post("/api/empresas", json={"razon_social": razon, "cuit": cuit, "domicilio": "X 1",
                                       "lugar_pago": "Bahía Blanca"})
    assert r.status_code == 201, r.json
    return r.json["id"]


@pytest.mark.parametrize("texto,esperado", [
    ("20222222223", "20-22222222-3"),
    ("30-71234567-1", "30-71234567-1"),
])
def test_cuit_valido(texto, esperado):
    assert normalizar_cuit(texto) == esperado


@pytest.mark.parametrize("texto", ["20-22222222-2", "123", "99-22222222-3", None])
def test_cuit_invalido(texto):
    with pytest.raises(ValueError):
        normalizar_cuit(texto)


def test_instalacion_nueva_trae_categorias_y_escalas_marcadas(client):
    conv = client.get("/api/convenios").json
    assert [c["codigo"] for c in conv] == ["CCT 130/75", "CCT 589/10"]
    assert len(conv[0]["categorias"]) == 21
    escalas = client.get("/api/escalas").json
    assert len(escalas) == 63
    verificadas = {e["categoria"] for e in escalas if e["verificada"]}
    assert verificadas == {"Auxiliar B"}
    assert all("sin verificar" in e["fuente"] for e in escalas)


def test_empresa_con_cuit_invalido_o_repetido(client):
    assert client.post("/api/empresas", json={"razon_social": "X", "cuit": "30-71234567-8",
                                          "domicilio": "Y"}).status_code == 400
    empresa(client)
    r = client.post("/api/empresas", json={"razon_social": "Otra", "cuit": "30712345671", "domicilio": "Y"})
    assert r.status_code == 400 and "Ya existe" in r.json["error"]


def test_importar_empleados_todo_o_nada(client):
    e = empresa(client)
    mala = planilla(client, [
        ["1", "Pérez", "Ana", "27-30123456-8", "", "Personal Auxiliar B", "01/02/2020", 8, None],
        ["2", "Gómez", "Luis", "20-22222222-2", "", "Auxiliar B", "01/02/2020", 8, None],
        ["3", "Ruiz", "Eva", "27-33333333-9", "", "Gerente", "01/02/2020", 9, None],
    ])
    r = importar(client, e, mala)
    assert r.status_code == 422
    assert len(r.json["detalle"]) == 2
    assert "dígito verificador" in r.json["detalle"][0]
    assert "categoría" in r.json["detalle"][1] and "jornada" in r.json["detalle"][1]
    assert client.get(f"/api/empresas/{e}/empleados").json == []


def test_mismo_cuil_en_dos_empresas_y_reimportar_actualiza(client):
    a, b = empresa(client), empresa(client, "30-70000000-8", "Dos SA")
    fila = ["7", "Pérez", "Ana", "27-30123456-8", "CCT 130/75", "Auxiliar B", "01/02/2020", 8, None]
    assert importar(client, a, planilla(client, [fila])).status_code == 201
    assert importar(client, b, planilla(client, [fila])).status_code == 201
    fila[7] = 4
    assert importar(client, a, planilla(client, [fila])).status_code == 201
    emp_a = client.get(f"/api/empresas/{a}/empleados").json
    assert len(emp_a) == 1 and emp_a[0]["jornada_horas"] == 4
    assert client.get(f"/api/empresas/{b}/empleados").json[0]["jornada_horas"] == 8
    assert {x["razon_social"]: x["empleados_activos"] for x in client.get("/api/empresas").json} == \
        {"Uno SRL": 1, "Dos SA": 1}


def test_liquidar_empresa_y_pdf_unico(client):
    e = empresa(client)
    importar(client, e, planilla(client, [
        ["1", "A", "Uno", "27-30123456-8", "", "Auxiliar B", "03/07/2017", 8, None],
        ["2", "B", "Dos", "20-22222222-3", "", "Auxiliar B", "01/09/2022", 8, None],
        ["3", "C", "Tres", "27-33333333-9", "", "Auxiliar B", "20/12/2004", 4, None],
        ["4", "D", "Cuatro", "20-44444444-5", "", "Vendedor A", "01/03/2025", 8, "31/08/2026"],
    ]))
    r = client.post(f"/api/empresas/{e}/liquidaciones", json={**PAGO, "periodo": "2026-09", "lugar_pago": ""})
    assert r.status_code == 201, r.json
    # D egresó en agosto: no se liquida en septiembre. Netos iguales a los recibos reales.
    assert sorted(x["neto"] for x in r.json["liquidaciones"]) == ["1194626.00", "1252060.00", "651388.00"]
    pdf = PdfReader(BytesIO(client.get(f"/api/empresas/{e}/recibos/2026-09.pdf").data))
    assert len(pdf.pages) == 6                           # 3 recibos × original y duplicado
    assert "Bahía Blanca" in pdf.pages[0].extract_text()  # lugar de pago de la empresa


def test_sac_por_empresa(client):
    e = empresa(client)
    importar(client, e, planilla(client, [
        ["1", "A", "Uno", "27-30123456-8", "", "Auxiliar B", "03/07/2017", 8, None],
        ["4", "D", "Cuatro", "20-44444444-5", "", "Vendedor A", "01/03/2025", 8, "31/08/2026"],
    ]))
    for periodo in ("2026-07", "2026-08", "2026-09"):
        client.post(f"/api/empresas/{e}/liquidaciones", json={**PAGO, "periodo": periodo})
    r = client.post(f"/api/empresas/{e}/sac", json={**PAGO, "periodo": "2026-12"})
    assert r.status_code == 201, r.json
    netos = {x["legajo"]: x for x in r.json["liquidaciones"]}
    assert set(netos) == {"1", "4"}                      # D trabajó jul-ago: también cobra SAC
    pdf = PdfReader(BytesIO(client.get(f"/api/empresas/{e}/recibos/2026-12.pdf?tipo=sac").data))
    assert "SAC 2° semestre 2026" in pdf.pages[0].extract_text()


def test_sac_fuera_de_junio_o_diciembre(client):
    e = empresa(client)
    importar(client, e, planilla(client, [["1", "A", "Uno", "27-30123456-8", "", "Auxiliar B",
                                           "03/07/2017", 8, None]]))
    emp = client.get(f"/api/empresas/{e}/empleados").json[0]["id"]
    client.post("/api/liquidaciones", json={**PAGO, "empleado_id": emp, "periodo": "2026-09"})
    r = client.post("/api/liquidaciones/sac", json={**PAGO, "empleado_id": emp, "periodo": "2026-09"})
    assert r.status_code == 400


def test_otro_convenio_se_da_de_alta_pero_no_se_liquida(client):
    r = client.post("/api/convenios", json={"codigo": "CCT 389/04", "nombre": "Gastronómicos (UTHGRA)",
                                        "categorias": ["Mozo"]})
    assert r.status_code == 201
    e = empresa(client)
    r = client.post("/api/empleados", json={
        "empresa_id": e, "apellido": "X", "nombre": "Y", "cuil": "20-22222222-3", "convenio": "CCT 389/04",
        "categoria": "Mozo", "fecha_ingreso": "2020-01-01"})
    assert r.status_code == 201
    r = client.post("/api/liquidaciones", json={**PAGO, "empleado_id": r.json["id"], "periodo": "2026-09"})
    assert r.status_code == 400 and "motor de cálculo" in r.json["error"]
