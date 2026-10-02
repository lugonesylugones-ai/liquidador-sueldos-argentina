from decimal import Decimal
from io import BytesIO

import pytest

from backend.app import create_app
from backend.escalas import CATEGORIAS_COMERCIO, generar_plantilla


@pytest.fixture
def app(tmp_path):
    return create_app({"DATABASE": str(tmp_path / "test.db"), "TESTING": True})


@pytest.fixture
def client(app):
    return app.test_client()


def subir(client, contenido: bytes, nombre: str = "escala.xlsx"):
    return client.post("/escalas/importar", data={"archivo": (BytesIO(contenido), nombre)},
                       content_type="multipart/form-data")


@pytest.fixture
def escala_cargada(client):
    from datetime import date
    montos = {c: Decimal("1000000") + 10000 * i for i, c in enumerate(CATEGORIAS_COMERCIO)}
    r = subir(client, generar_plantilla(montos, vigencia=date(2026, 7, 1)))
    assert r.status_code == 201, r.json
    return montos


@pytest.fixture
def empleado(client, escala_cargada):
    empresa = client.post("/empresas", json={
        "razon_social": "Test SA", "cuit": "30-11111111-1", "domicilio": "Calle 1"}).json["id"]
    return client.post("/empleados", json={
        "empresa_id": empresa, "apellido": "Gómez", "nombre": "Juan", "cuil": "20-22222222-2",
        "categoria": "Vendedor A", "fecha_ingreso": "2020-09-30", "afiliado_sindicato": False}).json["id"]
