"""Genera una escala de ejemplo y un recibo de punta a punta usando la API.

    python scripts/generar_ejemplo.py

Deja en ejemplos/ las escalas de julio a septiembre 2026 cargadas en la
plantilla y el recibo PDF (septiembre).
"""
import sys
import tempfile
from datetime import date
from io import BytesIO
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))

from backend.app import create_app  # noqa: E402
from openpyxl import load_workbook  # noqa: E402

from backend.datos_iniciales import filas_escala_2026  # noqa: E402
from backend.escalas import HOJA, agregar_fila, generar_plantilla  # noqa: E402

SALIDA = RAIZ / "ejemplos"

def escala_2026() -> bytes:
    wb = load_workbook(BytesIO(generar_plantilla()))
    ws = wb[HOJA]
    ws.delete_rows(2, ws.max_row)
    for cat, vigencia, basico, no_rem, asig in filas_escala_2026():
        agregar_fila(ws, cat, vigencia, basico, no_rem, asig or None)
    buf = BytesIO()
    wb.save(buf)
    return buf.getvalue()


EMPLEADOS_EJEMPLO = [
    # Datos ficticios. Los tres primeros replican los casos de tests/test_recibos_reales.py.
    ("0001", "Ejemplo", "Ana", "27-30123456-8", "Personal Auxiliar B", date(2017, 7, 3), 8, None),
    ("0002", "Ejemplo", "Bruno", "20-22222222-3", "Auxiliar B", date(2022, 9, 1), 8, None),
    ("0003", "Ejemplo", "Carla", "27-33333333-9", "Auxiliar B", date(2004, 12, 20), 4, None),
    ("0004", "Ejemplo", "Diego", "20-44444444-5", "Vendedores A", date(2025, 3, 1), 8, date(2026, 9, 30)),
]
PAGO = {"fecha_pago": "2026-10-03", "lugar_pago": "Ciudad Autónoma de Buenos Aires",
        "ultimo_deposito_periodo": "08/2026", "ultimo_deposito_fecha": "2026-09-10",
        "ultimo_deposito_banco": "Banco de la Nación Argentina"}


def planilla_empleados(contenido: bytes) -> bytes:
    wb = load_workbook(BytesIO(contenido))
    ws = wb["Empleados"]
    for legajo, apellido, nombre, cuil, cat, ingreso, horas, egreso in EMPLEADOS_EJEMPLO:
        ws.append([legajo, apellido, nombre, cuil, "CCT 130/75", cat, ingreso, horas, egreso])
    buf = BytesIO()
    wb.save(buf)
    return buf.getvalue()


def main() -> Path:
    SALIDA.mkdir(exist_ok=True)
    escala = escala_2026()
    (SALIDA / "escala_comercio_2026_jul_sep.xlsx").write_bytes(escala)

    with tempfile.TemporaryDirectory() as tmp:
        app = create_app({"DATABASE": str(Path(tmp) / "ejemplo.db"), "TESTING": True,
                          "CARGAR_ESCALAS_INICIALES": False})
        c = app.test_client()
        r = c.post("/api/escalas/importar", data={"archivo": (BytesIO(escala), "escala.xlsx")},
                   content_type="multipart/form-data")
        assert r.status_code == 201, r.json
        empresa = c.post("/api/empresas", json={
            "razon_social": "Almacén Ejemplo S.R.L.", "cuit": "30-71234567-1",
            "domicilio": "Av. Corrientes 1234, CABA"}).json["id"]
        empleados = planilla_empleados(c.get("/api/empleados/plantilla").data)
        (SALIDA / "empleados_ejemplo.xlsx").write_bytes(empleados)
        r = c.post(f"/api/empresas/{empresa}/empleados/importar",
                   data={"archivo": (BytesIO(empleados), "empleados.xlsx")}, content_type="multipart/form-data")
        assert r.status_code == 201, r.json
        for periodo in ("2026-07", "2026-08", "2026-09"):
            r = c.post(f"/api/empresas/{empresa}/liquidaciones", json={**PAGO, "periodo": periodo})
            assert r.status_code == 201 and not r.json["errores"], r.json
        # Diego egresa el 30/09: SAC proporcional del 2° semestre en septiembre.
        diego = next(e["id"] for e in c.get(f"/api/empresas/{empresa}/empleados").json if e["legajo"] == "0004")
        r = c.post("/api/liquidaciones/sac", json={**PAGO, "empleado_id": diego, "periodo": "2026-09"})
        assert r.status_code == 201, r.json

        mensual = c.get(f"/api/empresas/{empresa}/recibos/2026-09.pdf")
        sac = c.get(f"/api/empresas/{empresa}/recibos/2026-09.pdf?tipo=sac")
        assert mensual.status_code == sac.status_code == 200
        (SALIDA / "recibos_ejemplo_2026-09.pdf").write_bytes(mensual.data)
        (SALIDA / "recibo_sac_ejemplo.pdf").write_bytes(sac.data)
    print(f"Recibos generados en {SALIDA}")
    return SALIDA


if __name__ == "__main__":
    main()
