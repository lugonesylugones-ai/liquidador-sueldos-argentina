"""Archivos para el Libro de Sueldos Digital de ARCA (F.931): formato y bases imponibles."""
from decimal import Decimal

import pytest

from backend import arca
from backend.app import create_app

PAGO = {"fecha_pago": "2026-10-05", "lugar_pago": "Bahía Blanca", "ultimo_deposito_periodo": "08/2026",
        "ultimo_deposito_fecha": "2026-09-10", "ultimo_deposito_banco": "Banco Nación"}
LARGOS = {"01": 35, "02": 115, "03": 51, "04": 370}


@pytest.fixture
def client(tmp_path):
    return create_app({"DATABASE": str(tmp_path / "a.db"), "TESTING": True}).test_client()


def _importe(linea: str, desde: int, hasta: int) -> Decimal:
    return Decimal(int(linea[desde - 1:hasta])) / 100


def _bases(linea: str) -> dict:
    """Bruta, bases 1 a 9, base 10 y detracción de un registro '04'."""
    out = {"bruta": _importe(linea, 161, 175), 10: _importe(linea, 341, 355),
           "detraccion": _importe(linea, 356, 370)}
    for n in range(1, 10):
        out[n] = _importe(linea, 176 + 15 * (n - 1), 190 + 15 * (n - 1))
    return out


def _comercio(client) -> int:
    e = client.post("/api/empresas", json={"razon_social": "Uno SRL", "cuit": "30-71234567-1",
                                           "domicilio": "X 1", "lugar_pago": "Bahía Blanca"}).json["id"]
    for cuil, jornada, legajo in (("20-22222222-3", 8, "1"), ("27-33333333-9", 4, "2")):
        r = client.post("/api/empleados", json={
            "empresa_id": e, "legajo": legajo, "apellido": "Pérez", "nombre": legajo, "cuil": cuil,
            "categoria": "Auxiliar B", "fecha_ingreso": "2015-03-01", "jornada_horas": jornada})
        assert r.status_code == 201, r.json
    r = client.post(f"/api/empresas/{e}/liquidaciones", json={**PAGO, "periodo": "2026-09"})
    assert r.status_code == 201, r.json
    return e


def test_archivo_de_liquidacion_comercio(client):
    e = _comercio(client)
    r = client.get(f"/api/empresas/{e}/arca/2026-09.txt")
    assert r.status_code == 200 and "LSD_30712345671_202609.txt" in r.headers["Content-Disposition"]
    lineas = r.data.decode("cp1252").split("\r\n")[:-1]
    assert all(len(l) == LARGOS[l[:2]] for l in lineas)
    assert lineas[0] == "01" + "30712345671" + "SJ" + "202609" + "M" + "00001" + "30" + "000002"

    liqs = {l["legajo"]: l for l in client.get(f"/api/empresas/{e}/empleados").json}
    regs04 = [l for l in lineas if l[:2] == "04"]
    assert len(regs04) == 2
    for linea in regs04:
        b = _bases(linea)
        completa = linea[2:13] == "20222222223"
        assert linea[28:31] == ("008" if completa else "001")          # modalidad de contratación
        assert linea[62:68] == "126205"                                 # OSECAC por defecto
        assert b[1] == b[2] == b[3] == b[5]                             # remunerativo
        assert b[9] > b[1]                                              # ART también sobre el no rem.
        # Obra social sobre la jornada completa en la jornada parcial (como el F.931 real).
        assert b[4] == b[8] == (b[9] if completa else b[9] * 2)
        assert b["detraccion"] == (Decimal("7003.68") if completa else Decimal("3501.84"))
        assert b[10] == b[3] - b["detraccion"]

    # Los '03' de cada trabajador suman la remuneración bruta del '04' (haberes a crédito).
    for linea in regs04:
        cuil = linea[2:13]
        haberes = sum((_importe(l, 30, 44) * (1 if l[44] == "C" else -1)) for l in lineas
                      if l[:2] == "03" and l[2:13] == cuil and arca.CONCEPTOS[l[13:23].strip()].arca < "8")
        assert haberes == _bases(linea)["bruta"]
    assert liqs


def test_conceptos_y_datos_del_empleador(client):
    e = _comercio(client)
    texto = client.get(f"/api/empresas/{e}/arca/conceptos.txt").data.decode("cp1252")
    lineas = texto.split("\r\n")[:-1]
    assert all(len(l) == 195 for l in lineas)
    bas = next(l for l in lineas if l[6:16].strip() == "BAS")
    assert bas[:6] == "110000" and bas[167:186] == "1111111100010101000"
    nr = next(l for l in lineas if l[6:16].strip() == "NR")
    assert nr[:6] == "540000" and nr[167:186] == "0000111100000001000"

    assert client.get(f"/api/empresas/{e}/arca").json["cargados"] is False
    r = client.put(f"/api/empresas/{e}/arca", json={"tipo_empleador": "1", "actividad": "49", "zona": "4"})
    assert r.json == {"tipo_empleador": "1", "actividad": "049", "zona": "04", "cargados": True}
    linea = next(l for l in client.get(f"/api/empresas/{e}/arca/2026-09.txt").data.decode().split("\r\n")
                 if l[:2] == "04")
    assert linea[19] == "1" and linea[25:28] == "049" and linea[33:35] == "04"
    assert client.put(f"/api/empresas/{e}/arca", json={"tipo_empleador": "x", "actividad": "1",
                                                       "zona": "1"}).status_code == 400


def test_datos_del_trabajador_desde_la_web(client):
    e = _comercio(client)
    emp = client.get(f"/api/empresas/{e}/empleados").json[0]
    r = client.post(f"/empresas/{e}/empleados", data={
        "id": emp["id"], "apellido": emp["apellido"], "nombre": emp["nombre"], "cuil": emp["cuil"],
        "legajo": emp["legajo"], "convenio": "CCT 130/75", "categoria": "Auxiliar B",
        "fecha_ingreso": emp["fecha_ingreso"], "jornada_horas": emp["jornada_horas"],
        "obra_social": "104306", "conyuge": "1", "hijos": "2", "cbu": "0110000000000000000001"},
        follow_redirects=True)
    assert "guardado" in r.text
    lineas = client.get(f"/api/empresas/{e}/arca/2026-09.txt").data.decode().split("\r\n")
    cuil = emp["cuil"].replace("-", "")
    r02 = next(l for l in lineas if l[:2] == "02" and l[2:13] == cuil)
    r04 = next(l for l in lineas if l[:2] == "04" and l[2:13] == cuil)
    assert r02[73:95] == "0110000000000000000001" and r02[114] == "3"
    assert r04[13:16] == "102" and r04[62:68] == "104306"
    pagina = client.get(f"/empresas/{e}?editar={emp['id']}").text
    assert 'value="104306"' in pagina and "ARCA: Libro de Sueldos Digital" in pagina


def test_encargado_con_horas_extra_y_zona_aparte(client):
    r = client.post("/empresas/nueva", data={"razon_social": "Consorcio X", "cuit": "30712345671",
                                             "domicilio": "Calle 1", "lugar_pago": "Bahía Blanca"})
    e = int(r.headers["Location"].rstrip("/").split("/")[-1])
    client.post(f"/empresas/{e}/edificio", data={"categoria": "3", "unidades_funcionales": "20",
                                                 "zona_desfavorable": "1", "zona_recibo_aparte": "1"})
    client.post(f"/empresas/{e}/empleados", data={
        "apellido": "Pérez", "nombre": "Ana", "cuil": "27-33333333-9", "convenio": "CCT 589/10",
        "categoria": "Encargado Permanente sin vivienda", "fecha_ingreso": "2016-09-01", "afiliado": "1",
        "retira_residuos": "1", "tareas": ["jardin", "viaticos"]})
    emp = client.get(f"/api/empresas/{e}/empleados").json[0]
    r = client.post(f"/empresas/{e}/liquidar", data={**PAGO, "tipo": "mensual", "periodo": "2026-09",
                                                     f"horas_50_{emp['id']}": "10"}, follow_redirects=True)
    assert "Listo: 1 recibos" in r.text
    lineas = client.get(f"/api/empresas/{e}/arca/2026-09.txt").data.decode().split("\r\n")[:-1]
    assert all(len(l) == LARGOS[l[:2]] for l in lineas)
    he = next(l for l in lineas if l[13:23].strip() == "HE50")
    assert he[23:29] == "01000H"
    # La zona fría del recibo aparte va en el mismo envío, sumada al sueldo del mes.
    assert any(l[13:23].strip() == "ZONA" for l in lineas)
    b = _bases(next(l for l in lineas if l[:2] == "04"))
    assert b[1] == b[4] == b[8] == b[9] == b["bruta"] - sum(
        _importe(l, 30, 44) for l in lineas if l[13:23].strip() == "RED")
    assert next(l for l in lineas if l[:2] == "04")[62:68] == "106401"   # OSPERYH


def test_sin_liquidaciones_no_hay_archivo(client):
    e = client.post("/api/empresas", json={"razon_social": "Uno SRL", "cuit": "30-71234567-1",
                                           "domicilio": "X 1"}).json["id"]
    r = client.get(f"/api/empresas/{e}/arca/2026-09.txt")
    assert r.status_code == 400 and "No hay recibos" in r.json["error"]


def test_todo_concepto_del_liquidador_tiene_concepto_arca():
    from backend.calculo_suteryh import TAREAS
    assert set(TAREAS.values()) <= set(arca.CONCEPTOS)
    with pytest.raises(arca.ErrorArca):
        arca.concepto_arca("NUEVO")
