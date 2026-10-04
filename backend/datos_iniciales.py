"""Datos con los que arranca una instalación nueva.

- Convenios y categorías: estructura fija; se cargan siempre.
- Escalas de Comercio julio a septiembre 2026: se cargan si la tabla de escalas
  está vacía. Fuente: Ignacio Online, NO la circular oficial de FAECYS.
  Solo Auxiliar B está contrastada contra recibos reales; el resto queda
  marcado como no verificado hasta compararlo con el acuerdo.
"""
from datetime import date
import sqlite3

from .escalas import CATEGORIAS_COMERCIO, CONVENIO_COMERCIO

# (código, nombre, tiene motor de cálculo)
CONVENIOS = [
    (CONVENIO_COMERCIO, "Empleados de Comercio (FAECYS)", True),
]
CATEGORIAS = {CONVENIO_COMERCIO: CATEGORIAS_COMERCIO}

FUENTE_ESCALAS_2026 = "Ignacio Online, acuerdo julio 2026 (sin verificar contra circular FAECYS)"
VERIFICADAS_2026 = {"Auxiliar B": "Coincide con recibos reales jul-sep 2026"}

# Básicos de jornada completa en el orden de CATEGORIAS_COMERCIO.
BASICOS_2026 = {
    date(2026, 7, 1): [1137023, 1140294, 1151751, 1149298, 1154212, 1159120, 1173854, 1186128,
                       1204135, 1153389, 1159120, 1166487, 1153389, 1161573, 1188584, 1163214,
                       1177944, 1153389, 1177947, 1186128, 1204135],
    date(2026, 8, 1): [1160461, 1163793, 1175464, 1172965, 1177971, 1182970, 1197978, 1210482,
                       1228824, 1177132, 1182970, 1190474, 1177132, 1185469, 1212983, 1187140,
                       1202145, 1177132, 1202148, 1210482, 1228824],
    date(2026, 9, 1): [1183900, 1187292, 1199176, 1196632, 1201729, 1206821, 1222103, 1234836,
                       1253514, 1200875, 1206821, 1214462, 1200875, 1209365, 1237383, 1211067,
                       1226346, 1200875, 1226349, 1234836, 1253514],
}
# Inc. NR 100.000 + Recomp. NR 20.000
NO_REM_2026 = 120000
ASIG_UNICA_2026 = {date(2026, 7, 1): 25000, date(2026, 8, 1): 25000}


def filas_escala_2026():
    """(categoría, vigencia, básico, no rem, asig. única vez) de jul a sep 2026."""
    for vigencia, basicos in BASICOS_2026.items():
        for cat, basico in zip(CATEGORIAS_COMERCIO, basicos, strict=True):
            yield cat, vigencia, basico, NO_REM_2026, ASIG_UNICA_2026.get(vigencia, 0)


def cargar_estructura(conn: sqlite3.Connection) -> None:
    for codigo, nombre, motor in CONVENIOS:
        conn.execute("INSERT OR IGNORE INTO convenios (codigo, nombre, tiene_motor) VALUES (?, ?, ?)",
                     (codigo, nombre, int(motor)))
    for convenio, categorias in CATEGORIAS.items():
        for orden, cat in enumerate(categorias):
            conn.execute("INSERT OR IGNORE INTO categorias (convenio, nombre, orden) VALUES (?, ?, ?)",
                         (convenio, cat, orden))
    conn.commit()


def cargar_escalas_iniciales(conn: sqlite3.Connection) -> int:
    """Carga las escalas 2026 solo si no hay ninguna escala. Devuelve filas cargadas."""
    if conn.execute("SELECT 1 FROM escalas LIMIT 1").fetchone():
        return 0
    n = 0
    for cat, vigencia, basico, no_rem, asig in filas_escala_2026():
        verif = VERIFICADAS_2026.get(cat)
        conn.execute(
            """INSERT INTO escalas (convenio, categoria, monto, vigencia_desde, no_remunerativo,
                                    asignacion_unica, fuente, verificada)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (CONVENIO_COMERCIO, cat, f"{basico}.00", vigencia.isoformat(), f"{no_rem}.00",
             f"{asig}.00", f"{FUENTE_ESCALAS_2026}. {verif}" if verif else FUENTE_ESCALAS_2026,
             1 if verif else 0))
        n += 1
    conn.commit()
    return n
