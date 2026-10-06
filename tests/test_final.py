"""Liquidación final (egreso), meses incompletos y convenios cargados desde la web."""
import sqlite3
from datetime import date
from decimal import Decimal as D
from io import BytesIO

import pytest
from pypdf import PdfReader

from backend.app import create_app
from backend.calculo import (anios_indemnizacion, dias_del_mes, dias_vacaciones_anuales,
                             en_periodo_de_prueba, liquidar_comercio, liquidar_final)

ESCALAS = [("2026-07", "1161573", 25000), ("2026-08", "1185469", 25000), ("2026-09", "1209365", 0)]
PAGO = {"fecha_pago": "2026-10-20", "lugar_pago": "Bahía Blanca", "ultimo_deposito_periodo": "09/2026",
        "ultimo_deposito_fecha": "2026-10-10", "ultimo_deposito_banco": "ICBC"}


def historial(ingreso=date(2017, 7, 3), horas=8):
    return [liquidar_comercio(periodo=p, categoria="Auxiliar B", basico=D(b), no_remunerativo=D(120000),
                              vigencia_escala=date(2026, int(p[5:]), 1), fecha_ingreso=ingreso,
                              asignacion_extraordinaria=D(e), jornada_horas=horas) for p, b, e in ESCALAS]


def final(causa="despido_sin_causa", egreso=date(2026, 10, 15), ingreso=date(2017, 7, 3), **kw):
    return liquidar_final(categoria="Auxiliar B", basico=D("1209365"), no_remunerativo=D(120000),
                          vigencia_escala=date(2026, 9, 1), fecha_ingreso=ingreso, fecha_egreso=egreso,
                          causa=causa, historial=historial(ingreso), **kw)


def importes(liq):
    return {c.codigo: c.importe for c in liq.conceptos}


def test_dias_del_mes():
    assert dias_del_mes("2026-10", date(2017, 7, 3), date(2026, 10, 15)) == 15
    assert dias_del_mes("2026-10", date(2026, 10, 20)) == 12
    assert dias_del_mes("2026-10", date(2017, 7, 3), date(2026, 10, 31)) == 30
    assert dias_del_mes("2026-02", date(2017, 7, 3), date(2026, 2, 28)) == 30
    assert dias_del_mes("2026-10", date(2026, 11, 1)) == 0


def test_reglas_de_antiguedad():
    assert [dias_vacaciones_anuales(date(2026 - a, 12, 31), 2026) for a in (1, 5, 10, 20)] == [14, 21, 28, 35]
    assert anios_indemnizacion(date(2017, 7, 3), date(2026, 10, 3)) == 9    # 9 años y 3 meses justos
    assert anios_indemnizacion(date(2017, 7, 3), date(2026, 10, 4)) == 10   # fracción mayor de 3 meses
    assert en_periodo_de_prueba(date(2026, 5, 1), date(2026, 10, 15))       # Ley 27.742: 6 meses
    assert not en_periodo_de_prueba(date(2024, 1, 1), date(2024, 4, 2))    # antes: 3 meses


def test_mes_de_ingreso_se_paga_por_dias():
    liq = liquidar_comercio(periodo="2026-09", categoria="Auxiliar B", basico=D("1209365"),
                            vigencia_escala=date(2026, 9, 1), fecha_ingreso=date(2026, 9, 16), dias=15)
    assert importes(liq)["BAS"] == D("604682.50") and liq.dias_trabajados == 15
    assert importes(liq)["PRES"] == D("50370.05")   # 8,33% de 604.682,50 (sin antigüedad)


def test_despido_sin_causa_sin_preaviso():
    liq = final()
    i = importes(liq)
    # Mes trabajado: 15 días; mes completo de referencia: 1.428.014,56 rem + 141.695,64 no rem.
    assert i["BAS"] == D("604682.50")
    assert i["SAC"] == D("415210.76")                        # 50% × 1.428.014,56 × 107/184
    assert i["VAC"] == D("946488.05")                        # 21 días × 288/365 = 16,57 × mes / 25
    assert i["IND"] == D("15697102.00")                      # 10 años × 1.569.710,20
    assert i["PREAV"] == D("3139420.40")                     # 2 meses (más de 5 años)
    assert i["INTEG"] == D("784855.10")                      # 15 días que faltan
    assert i["SACPREAV"] == D("261618.37") and i["SACINTEG"] == D("65404.59")
    # Lo indemnizatorio no lleva aportes: la jubilación es solo sobre mes + SAC.
    assert i["JUB"] == (liq.total_remunerativo * D("0.11")).quantize(D("0.01"))
    assert liq.tipo == "final" and liq.egreso["causa"] == "despido_sin_causa"
    assert liq.neto == liq.total_remunerativo + liq.total_no_remunerativo - liq.total_descuentos


def test_renuncia_y_preaviso_otorgado_no_indemnizan():
    renuncia = importes(final("renuncia"))
    assert "IND" not in renuncia and "PREAV" not in renuncia and "VAC" in renuncia and "SAC" in renuncia
    con_preaviso = importes(final(preaviso_otorgado=True))
    assert "IND" in con_preaviso and "PREAV" not in con_preaviso and "INTEG" not in con_preaviso


def test_fallecimiento_paga_la_mitad_y_tope_con_piso_vizzoti():
    assert importes(final("fallecimiento"))["IND"] == D("7848551.00")
    topeada = importes(final(tope_indemnizatorio=D("900000")))["IND"]
    assert topeada == D("10517058.30")                      # 67% de 1.569.710,20 × 10, no el tope
    assert importes(final(tope_indemnizatorio=D("1200000")))["IND"] == D("12000000.00")


def test_periodo_de_prueba_y_vacaciones_gozadas():
    i = importes(final(ingreso=date(2026, 7, 1), egreso=date(2026, 10, 15)))
    assert "IND" not in i
    assert i["PREAV"] == D("720050.55")                      # 15 días de 1.440.101,10, sin antigüedad
    sin = importes(final())["VAC"]
    con = importes(final(vacaciones_gozadas=D("10")))["VAC"]
    assert con == (sin / D("16.57") * D("6.57")).quantize(D("0.01"))


# --- Desde la web --------------------------------------------------------------
@pytest.fixture
def client(tmp_path):
    return create_app({"DATABASE": str(tmp_path / "f.db"), "TESTING": True}).test_client()


def preparar(client):
    e = client.post("/api/empresas", json={"razon_social": "Uno SRL", "cuit": "30-71234567-1",
                                           "domicilio": "X 1", "lugar_pago": "Bahía Blanca"}).json["id"]
    emp = client.post("/api/empleados", json={
        "empresa_id": e, "apellido": "Pérez", "nombre": "Ana", "cuil": "27-33333333-9",
        "categoria": "Auxiliar B", "fecha_ingreso": "2017-07-03"}).json["id"]
    for p in ("2026-07", "2026-08", "2026-09"):
        assert client.post(f"/api/empresas/{e}/liquidaciones", json={**PAGO, "periodo": p}).status_code == 201
    return e, emp


def test_final_desde_la_web_reemplaza_el_mes_y_bloquea_el_sac(client):
    e, emp = preparar(client)
    assert "Liquidación final" in client.get(f"/empresas/{e}/empleados/{emp}/final").text
    r = client.post(f"/empresas/{e}/empleados/{emp}/final",
                    data={**PAGO, "fecha_egreso": "2026-09-15", "causa": "despido_sin_causa"},
                    follow_redirects=True)
    assert "Liquidación final lista" in r.text and "Indemnización por antigüedad" in r.text
    assert "sin aportes" in r.text
    # El sueldo de septiembre se reemplazó por la final y el empleado quedó con egreso.
    assert client.get(f"/api/empresas/{e}/empleados").json[0]["fecha_egreso"] == "2026-09-15"
    assert "Liquidaciones finales 2026-09" in client.get(f"/empresas/{e}/liquidaciones/2026-09?tipo=final").text
    assert client.get(f"/api/empresas/{e}/recibos/2026-09.pdf").status_code == 404
    # Ni el mes ni el SAC se le vuelven a liquidar.
    r = client.post(f"/api/empresas/{e}/sac", json={**PAGO, "periodo": "2026-12"})
    assert r.status_code == 400 and r.json["liquidaciones"] == [] and r.json["errores"] == []
    r = client.post("/api/liquidaciones", json={**PAGO, "empleado_id": emp, "periodo": "2026-09"})
    assert r.status_code == 400 and "liquidación final" in r.json["error"]

    pdf = PdfReader(BytesIO(client.get(f"/api/empresas/{e}/recibos/2026-09.pdf?tipo=final").data))
    texto = pdf.pages[0].extract_text()
    assert "liquidación final" in texto and "Despido sin causa" in texto and "(sin aportes)" in texto


def test_final_por_api_y_errores(client):
    e, emp = preparar(client)
    r = client.post("/api/liquidaciones/final", json={**PAGO, "empleado_id": emp, "fecha_egreso": "2026-10-31",
                                                     "causa": "renuncia"})
    assert r.status_code == 201 and r.json["tipo"] == "final"
    r = client.post("/api/liquidaciones/final", json={**PAGO, "empleado_id": emp, "fecha_egreso": "2016-01-01",
                                                     "causa": "renuncia"})
    assert r.status_code == 400 and "anterior" in r.json["error"]
    r = client.post("/api/liquidaciones/final", json={**PAGO, "empleado_id": emp, "fecha_egreso": "2026-10-31",
                                                     "causa": "jubilacion"})
    assert r.status_code == 400


def test_base_vieja_se_migra(tmp_path):
    ruta = tmp_path / "vieja.db"
    conn = sqlite3.connect(ruta)
    conn.executescript("""
        CREATE TABLE liquidaciones (id INTEGER PRIMARY KEY AUTOINCREMENT, empleado_id INTEGER NOT NULL,
            periodo TEXT NOT NULL, tipo TEXT NOT NULL DEFAULT 'mensual' CHECK (tipo IN ('mensual', 'sac')),
            fecha_pago TEXT NOT NULL, lugar_pago TEXT NOT NULL, resultado TEXT NOT NULL,
            creada TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, UNIQUE (empleado_id, periodo, tipo));
        INSERT INTO liquidaciones (empleado_id, periodo, fecha_pago, lugar_pago, resultado)
            VALUES (1, '2026-09', '2026-10-05', 'X', '{}');""")
    conn.commit()
    conn.close()
    create_app({"DATABASE": str(ruta), "TESTING": True})
    conn = sqlite3.connect(ruta)
    assert conn.execute("SELECT COUNT(*) FROM liquidaciones").fetchone()[0] == 1
    conn.execute("INSERT INTO liquidaciones (empleado_id, periodo, tipo, fecha_pago, lugar_pago, resultado) "
                 "VALUES (1, '2026-10', 'final', 'x', 'x', '{}')")


def test_convenio_nuevo_con_categorias_y_escala(client):
    from backend.escalas import generar_plantilla
    r = client.post("/convenios/nuevo", data={"codigo": "CCT 389/04", "nombre": "Gastronómicos",
                                              "categorias": "Encargado permanente\nAyudante\n"},
                    follow_redirects=True)
    assert "Convenio CCT 389/04 creado" in r.text and "Sin cálculo todavía" in r.text
    r = client.post("/convenios/categorias", data={"codigo": "CCT 389/04", "categorias": "Suplente\nAyudante"},
                    follow_redirects=True)
    assert "Se agregaron 1 categorías" in r.text
    cats = client.get("/api/convenios").json
    assert next(c for c in cats if c["codigo"] == "CCT 389/04")["categorias"] == \
        ["Encargado permanente", "Ayudante", "Suplente"]

    plantilla = client.get("/api/escalas/plantilla?convenio=CCT 389/04").data
    from openpyxl import load_workbook
    assert [r[0].value for r in load_workbook(BytesIO(plantilla))["Escala"].iter_rows(min_row=2)] == \
        ["Encargado permanente", "Ayudante", "Suplente"]
    contenido = generar_plantilla({"encargado permanente": D("900000"), "Ayudante": D("800000"),
                                   "Suplente": D("700000")}, vigencia=date(2026, 10, 1),
                                  categorias=["encargado permanente", "Ayudante", "Suplente"])
    r = client.post("/escalas/importar", data={"convenio": "CCT 389/04",
                                               "archivo": (BytesIO(contenido), "gastro.xlsx")},
                    follow_redirects=True)
    assert "Se cargaron 3 filas" in r.text and "Encargado permanente" in r.text
    assert len(client.get("/api/escalas?convenio=CCT 389/04").json) == 3
    # Una categoría de Comercio no entra en otro convenio.
    otra = generar_plantilla({"Vendedor A": D("1")}, vigencia=date(2026, 10, 1), categorias=["Vendedor A"])
    r = client.post("/escalas/importar", data={"convenio": "CCT 389/04",
                                               "archivo": (BytesIO(otra), "x.xlsx")}, follow_redirects=True)
    assert "categoría desconocida" in r.text


def test_paginas_nuevas_responden(client):
    preparar(client)
    tablero = client.get("/").text
    assert "Empleados activos" in tablero and "2026-09" in tablero
    for url in ("/empresas", "/convenios", "/escalas?convenio=CCT 130/75"):
        assert client.get(url).status_code == 200
    assert client.get("/escalas?convenio=NO EXISTE").status_code == 404
