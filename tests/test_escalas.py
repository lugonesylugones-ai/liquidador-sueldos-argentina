from datetime import date, datetime
from decimal import Decimal as D
from io import BytesIO

from openpyxl import Workbook

import pytest

from backend.escalas import COLUMNAS, leer_plantilla, normalizar_categoria
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


def test_columna_no_remunerativo_opcional():
    res = leer_plantilla(xlsx([
        ["Auxiliar B", 1209365, "01/09/2026", 120000],
        ["Auxiliar A", 1200000, "01/09/2026", None],
        ["Auxiliar C", 1220000, "01/09/2026", "-5"],
    ]))
    assert [(f.categoria, f.no_remunerativo) for f in res.filas] == [
        ("Auxiliar B", D("120000.00")), ("Auxiliar A", D("0"))]
    assert res.errores == ["Fila 4: no remunerativo: el monto tiene que ser mayor a cero: '-5'"]


def test_plantilla_vieja_de_tres_columnas_sigue_funcionando():
    res = leer_plantilla(xlsx([["Vendedor A", 1000, "01/07/2026"]], encabezado=COLUMNAS[:3]))
    assert res.ok and res.filas[0].no_remunerativo == D("0")


@pytest.mark.parametrize("texto,esperado", [
    ("Personal Auxiliar B", "Auxiliar B"),
    ("Auxiliar especializado A", "Auxiliar Especializado A"),
    ("VENDEDORES  D", "Vendedor D"),
    ('Cajeros "B"', "Cajero B"),
    ("administrativo f", "Administrativo F"),
    ("Vendedor E", None),
    ("Gerente A", None),
])
def test_normaliza_nombres_de_categoria(texto, esperado):
    assert normalizar_categoria(texto) == esperado


def test_columna_asignacion_unica():
    res = leer_plantilla(xlsx([["Auxiliar B", 1161573, "01/07/2026", 120000, 25000]]))
    assert res.ok and res.filas[0].asignacion_unica == D("25000.00")
