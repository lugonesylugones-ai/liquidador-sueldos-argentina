from datetime import date, datetime
from decimal import Decimal as D
from io import BytesIO

from openpyxl import Workbook

from backend.escalas import COLUMNAS, leer_plantilla
from tests.conftest import subir


def xlsx(filas, hoja="Escala", encabezado=COLUMNAS):
    wb = Workbook()
    ws = wb.active
    ws.title = hoja
    ws.append(encabezado)
    for f in filas:
        ws.append(f)
    buf = BytesIO()
    wb.save(buf)
    return buf.getvalue()


def test_lee_formatos_de_monto_y_fecha():
    res = leer_plantilla(xlsx([
        ["Vendedor A", 1050000.5, datetime(2026, 7, 1)],
        ["vendedor b", "$ 1.100.000,25", "01/07/2026"],
        ["Cajero A", "1200000", "2026-08-01"],
    ]))
    assert res.ok, res.errores
    assert [(f.categoria, f.monto, f.vigencia_desde) for f in res.filas] == [
        ("Vendedor A", D("1050000.50"), date(2026, 7, 1)),
        ("Vendedor B", D("1100000.25"), date(2026, 7, 1)),
        ("Cajero A", D("1200000.00"), date(2026, 8, 1)),
    ]


def test_ignora_filas_vacias():
    res = leer_plantilla(xlsx([["Vendedor A", 1, "01/07/2026"], [None, None, None]]))
    assert res.ok and len(res.filas) == 1


def test_reporta_errores_por_fila():
    res = leer_plantilla(xlsx([
        ["Gerente", 1000, "01/07/2026"],
        ["Vendedor A", -5, "01/07/2026"],
        ["Vendedor B", 1000, "31/02/2026"],
        ["Vendedor C", 1000, "01/07/2026"],
        ["Vendedor C", 2000, "01/07/2026"],
    ]))
    assert not res.ok
    assert len(res.errores) == 4
    assert res.errores[0].startswith("Fila 2: categoría desconocida")
    assert "mayor a cero" in res.errores[1]
    assert "fecha inválida" in res.errores[2]
    assert res.errores[3].startswith("Fila 6") and "repetida" in res.errores[3]


def test_hoja_o_encabezado_incorrecto():
    assert "Falta la hoja" in leer_plantilla(xlsx([], hoja="Otra")).errores[0]
    assert "Encabezado" in leer_plantilla(xlsx([], encabezado=("A", "B", "C"))).errores[0]
    assert "No se pudo abrir" in leer_plantilla(b"no es un excel").errores[0]


def test_plantilla_descargable_se_puede_reimportar(client):
    r = client.get("/escalas/plantilla")
    assert r.status_code == 200
    # Sin montos completados la plantilla se rechaza entera.
    r = subir(client, r.data)
    assert r.status_code == 422
    assert client.get("/escalas").json == []


def test_importar_con_errores_no_carga_nada(client):
    r = subir(client, xlsx([["Vendedor A", 1000, "01/07/2026"], ["Gerente", 1, "01/07/2026"]]))
    assert r.status_code == 422
    assert client.get("/escalas").json == []


def test_reimportar_misma_vigencia_actualiza_y_otra_vigencia_suma(client):
    subir(client, xlsx([["Vendedor A", 1000, "01/07/2026"]]))
    subir(client, xlsx([["Vendedor A", 1500, "01/07/2026"]]))
    subir(client, xlsx([["Vendedor A", 2000, "01/09/2026"]]))
    escalas = client.get("/escalas").json
    assert [(e["monto"], e["vigencia_desde"]) for e in escalas] == [
        ("2000.00", "2026-09-01"), ("1500.00", "2026-07-01")]


def test_solo_xlsx(client):
    r = client.post("/escalas/importar", data={"archivo": (BytesIO(b"x"), "escala.csv")},
                    content_type="multipart/form-data")
    assert r.status_code == 400
