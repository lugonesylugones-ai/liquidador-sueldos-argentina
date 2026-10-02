"""Genera una escala de ejemplo y un recibo de punta a punta usando la API.

    python scripts/generar_ejemplo.py

Deja en ejemplos/ la escala de septiembre 2026 cargada en la plantilla y el
recibo PDF.
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

# Escala septiembre 2026 (acuerdo julio 2026) según la publicación de Ignacio Online,
# no la circular de FAECYS. No remunerativo = Inc. NR 100.000 + Recomp. NR 20.000.
# Auxiliar B coincide con los recibos reales de septiembre; el resto no está contrastado.
BASICOS_SEP_2026 = {
    "Maestranza A": 1183900, "Maestranza B": 1187292, "Maestranza C": 1199176,
    "Administrativo A": 1196632, "Administrativo B": 1201729, "Administrativo C": 1206821,
    "Administrativo D": 1222103, "Administrativo E": 1234836, "Administrativo F": 1253514,
    "Cajero A": 1200875, "Cajero B": 1206821, "Cajero C": 1214462,
    "Auxiliar A": 1200875, "Auxiliar B": 1209365, "Auxiliar C": 1237383,
    "Auxiliar Especializado A": 1211067, "Auxiliar Especializado B": 1226346,
    "Vendedor A": 1200875, "Vendedor B": 1226349, "Vendedor C": 1234836, "Vendedor D": 1253514,
}
NO_REM_SEP_2026 = Decimal("120000")
MONTOS_EJEMPLO = {cat: (Decimal(m), NO_REM_SEP_2026) for cat, m in BASICOS_SEP_2026.items()}


def main() -> Path:
    SALIDA.mkdir(exist_ok=True)
    plantilla = generar_plantilla(MONTOS_EJEMPLO, vigencia=date(2026, 9, 1))
    (SALIDA / "escala_comercio_sep2026.xlsx").write_bytes(plantilla)

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
