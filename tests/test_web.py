"""Pantallas web: el flujo completo con formularios, como lo usaría alguien desde el navegador."""
from io import BytesIO

import pytest
from pypdf import PdfReader

from backend.app import create_app

PAGO = {"fecha_pago": "2026-10-03", "lugar_pago": "Bahía Blanca", "ultimo_deposito_periodo": "08/2026",
        "ultimo_deposito_fecha": "2026-09-10", "ultimo_deposito_banco": "Banco Nación"}


@pytest.fixture
def client(tmp_path):
    return create_app({"DATABASE": str(tmp_path / "w.db"), "TESTING": True}).test_client()


def crear_empresa(client) -> int:
    r = client.post("/empresas/nueva", data={"razon_social": "Uno SRL", "cuit": "30712345671",
                                             "domicilio": "X 1", "lugar_pago": "Bahía Blanca"})
    assert r.status_code == 302
    return int(r.headers["Location"].rstrip("/").split("/")[-1])


def alta(client, e, **campos):
    datos = {"apellido": "Pérez", "nombre": "Ana", "cuil": "27-33333333-9", "convenio": "CCT 130/75",
             "categoria": "Vendedor B", "fecha_ingreso": "2019-03-01", "jornada_horas": "8", **campos}
    return client.post(f"/empresas/{e}/empleados", data=datos, follow_redirects=True)


def test_paginas_responden(client):
    assert "Nueva empresa" in client.get("/").text
    escalas = client.get("/escalas").text
    assert "Sin verificar" in escalas and "Verificada" in escalas and "2026-09-01" in escalas


def test_flujo_completo_desde_formularios(client):
    e = crear_empresa(client)
    assert "Uno SRL" in client.get("/").text

    r = alta(client, e)
    assert "guardado" in r.text and "Pérez, Ana" in r.text
    alta(client, e, apellido="Gómez", nombre="Juan", cuil="20-22222222-3", categoria="Auxiliar B",
         jornada_horas="4")
    emp_id = client.get(f"/api/empresas/{e}/empleados").json[1]["id"]  # Pérez

    form = client.get(f"/empresas/{e}/liquidar?periodo=2026-09&tipo=mensual").text
    assert f'name="inasistencias_{emp_id}"' in form and 'value="Bahía Blanca"' in form

    r = client.post(f"/empresas/{e}/liquidar", data={**PAGO, "tipo": "mensual", "periodo": "2026-09",
                                                     f"inasistencias_{emp_id}": "1"},
                    follow_redirects=True)
    assert "Listo: 2 recibos de 2026-09" in r.text and "Total a pagar" in r.text
    pdf = PdfReader(BytesIO(client.get(f"/api/empresas/{e}/recibos/2026-09.pdf").data))
    texto = "".join(p.extract_text() for p in pdf.pages)
    assert len(pdf.pages) == 4 and "Inasistencias" in texto

    # La segunda vez el formulario avisa y precarga el último depósito usado.
    form = client.get(f"/empresas/{e}/liquidar?periodo=2026-09&tipo=mensual").text
    assert "ya está liquidado" in form and 'value="Banco Nación"' in form
    # Y el período sugerido pasa al mes siguiente.
    assert 'value="2026-10"' in client.get(f"/empresas/{e}").text


def test_errores_se_muestran_sin_romper(client):
    r = client.post("/empresas/nueva", data={"razon_social": "X", "cuit": "30-71234567-8", "domicilio": "Y"},
                    follow_redirects=True)
    assert "CUIT" in r.text and 'class="msg error"' in r.text

    e = crear_empresa(client)
    r = alta(client, e, categoria="Gerente General")
    assert 'class="msg error"' in r.text

    r = client.post(f"/empresas/{e}/empleados/importar",
                    data={"archivo": (BytesIO(b"x"), "empleados.csv")}, follow_redirects=True)
    assert "Solo se aceptan archivos .xlsx" in r.text

    # Sin empleados no hay nada que liquidar; falta el banco: vuelve al formulario con el error.
    alta(client, e)
    datos = {**PAGO, "tipo": "mensual", "periodo": "2026-09", "ultimo_deposito_banco": ""}
    r = client.post(f"/empresas/{e}/liquidar", data=datos, follow_redirects=True)
    assert "ultimo_deposito_banco" in r.text and "Liquidar" in r.text


def test_editar_empleado_y_egreso(client):
    e = crear_empresa(client)
    alta(client, e)
    emp = client.get(f"/api/empresas/{e}/empleados").json[0]
    assert 'value="Pérez"' in client.get(f"/empresas/{e}?editar={emp['id']}").text
    r = alta(client, e, fecha_egreso="2026-09-15", jornada_horas="6")
    assert "guardado" in r.text
    emp = client.get(f"/api/empresas/{e}/empleados").json[0]
    assert emp["fecha_egreso"] == "2026-09-15" and emp["jornada_horas"] == 6
    r = alta(client, e, fecha_egreso="2010-01-01")
    assert "anterior a la de ingreso" in r.text


def test_aguinaldo_desde_la_web(client):
    e = crear_empresa(client)
    alta(client, e, fecha_egreso="2026-09-30")
    for periodo in ("2026-07", "2026-08", "2026-09"):
        client.post(f"/empresas/{e}/liquidar", data={**PAGO, "tipo": "mensual", "periodo": periodo})
    form = client.get(f"/empresas/{e}/liquidar?periodo=2026-09&tipo=sac").text
    assert "inasistencias_" not in form
    r = client.post(f"/empresas/{e}/liquidar", data={**PAGO, "tipo": "sac", "periodo": "2026-09"},
                    follow_redirects=True)
    assert "Listo: 1 recibos de aguinaldo 2026-09" in r.text
    assert client.get(f"/api/empresas/{e}/recibos/2026-09.pdf?tipo=sac").status_code == 200


def test_subir_escala_desde_la_web(client):
    from datetime import date
    from decimal import Decimal

    from backend.escalas import CATEGORIAS_COMERCIO, generar_plantilla
    incompleta = generar_plantilla({"Vendedor A": Decimal("2000000")}, vigencia=date(2026, 10, 1))
    r = client.post("/escalas/importar", data={"archivo": (BytesIO(incompleta), "oct.xlsx")},
                    follow_redirects=True)
    assert "no se cargó nada" in r.text and "Fila 2: monto inválido" in r.text

    completa = generar_plantilla({c: Decimal("2000000") for c in CATEGORIAS_COMERCIO}, vigencia=date(2026, 10, 1))
    r = client.post("/escalas/importar", data={"archivo": (BytesIO(completa), "oct.xlsx")},
                    follow_redirects=True)
    assert "Se cargaron 21 filas" in r.text and "2026-10-01" in r.text and "Importada de oct.xlsx" in r.text
