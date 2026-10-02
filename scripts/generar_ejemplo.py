"""Genera una escala de ejemplo y un recibo de punta a punta usando la API.

    python scripts/generar_ejemplo.py

Deja en ejemplos/ la plantilla con montos y el recibo PDF. El básico de
Auxiliar B y la suma no remunerativa son los de septiembre 2026 que figuran en
recibos reales; el resto de las categorías son ILUSTRATIVAS.
"""
import sys
import tempfile
from datetime import date
from decimal import Decimal
from io import BytesIO
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))

from backend.app import create_app  # noqa: E402
from backend.escalas import CATEGORIAS_COMERCIO, generar_plantilla  # noqa: E402

SALIDA = RAIZ / "ejemplos"

# Ilustrativos salvo Auxiliar B: arranca en 1.180.000 y sube 0,5% por categoría.
MONTOS_EJEMPLO = {
    cat: ((Decimal("1180000") * Decimal("1.005") ** i).quantize(Decimal("0.01")), Decimal("120000"))
    for i, cat in enumerate(CATEGORIAS_COMERCIO)
}
MONTOS_EJEMPLO["Auxiliar B"] = (Decimal("1209365"), Decimal("120000"))


def main() -> Path:
    SALIDA.mkdir(exist_ok=True)
    plantilla = generar_plantilla(MONTOS_EJEMPLO, vigencia=date(2026, 9, 1))
    (SALIDA / "escala_comercio_ejemplo.xlsx").write_bytes(plantilla)

    with tempfile.TemporaryDirectory() as tmp:
        app = create_app({"DATABASE": str(Path(tmp) / "ejemplo.db"), "TESTING": True})
        c = app.test_client()
        r = c.post("/escalas/importar", data={"archivo": (BytesIO(plantilla), "escala.xlsx")},
                   content_type="multipart/form-data")
        assert r.status_code == 201, r.json
        empresa = c.post("/empresas", json={
            "razon_social": "Almacén Ejemplo S.R.L.", "cuit": "30-71234567-8",
            "domicilio": "Av. Corrientes 1234, CABA"}).json["id"]
        empleado = c.post("/empleados", json={
            "empresa_id": empresa, "apellido": "Pérez", "nombre": "María Laura",
            "cuil": "27-30123456-4", "categoria": "Auxiliar B",
            "fecha_ingreso": "2017-07-03", "jornada_horas": 8}).json["id"]
        r = c.post("/liquidaciones", json={
            "empleado_id": empleado, "periodo": "2026-09", "inasistencias_injustificadas": 0,
            "fecha_pago": "2026-10-03", "lugar_pago": "Ciudad Autónoma de Buenos Aires",
            "ultimo_deposito_periodo": "08/2026", "ultimo_deposito_fecha": "2026-09-10",
            "ultimo_deposito_banco": "Banco de la Nación Argentina"})
        assert r.status_code == 201, r.json
        pdf = c.get(f"/liquidaciones/{r.json['id']}/recibo.pdf")
        assert pdf.status_code == 200
        destino = SALIDA / "recibo_ejemplo.pdf"
        destino.write_bytes(pdf.data)
    print(f"Recibo generado en {destino}")
    return destino


if __name__ == "__main__":
    main()
