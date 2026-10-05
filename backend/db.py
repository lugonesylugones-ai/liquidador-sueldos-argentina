import sqlite3

from flask import current_app, g

SCHEMA = """
CREATE TABLE IF NOT EXISTS convenios (
    codigo TEXT PRIMARY KEY,          -- ej. 'CCT 130/75'
    nombre TEXT NOT NULL,
    tiene_motor INTEGER NOT NULL DEFAULT 0   -- 1 si el liquidador sabe calcularlo
);

CREATE TABLE IF NOT EXISTS categorias (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    convenio TEXT NOT NULL REFERENCES convenios(codigo),
    nombre TEXT NOT NULL,
    orden INTEGER NOT NULL DEFAULT 0,
    UNIQUE (convenio, nombre)
);

CREATE TABLE IF NOT EXISTS empresas (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    razon_social TEXT NOT NULL,
    cuit TEXT NOT NULL UNIQUE,
    domicilio TEXT NOT NULL,
    lugar_pago TEXT                   -- default para los recibos
);

-- Datos del edificio para los consorcios (CCT 589/10), 1 a 1 con la empresa.
CREATE TABLE IF NOT EXISTS edificios (
    empresa_id INTEGER PRIMARY KEY REFERENCES empresas(id),
    categoria INTEGER NOT NULL CHECK (categoria BETWEEN 1 AND 4),   -- art. 6, por servicios centrales
    unidades_funcionales INTEGER NOT NULL DEFAULT 0 CHECK (unidades_funcionales >= 0),
    zona_desfavorable INTEGER NOT NULL DEFAULT 0,
    -- Base de la zona fría: 'remunerativo' (todo) o 'basico_antiguedad'; y si va en recibo aparte.
    zona_base TEXT NOT NULL DEFAULT 'remunerativo' CHECK (zona_base IN ('remunerativo', 'basico_antiguedad')),
    zona_recibo_aparte INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS escalas (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    convenio TEXT NOT NULL REFERENCES convenios(codigo),
    categoria TEXT NOT NULL,
    monto TEXT NOT NULL,              -- Decimal guardado como texto
    vigencia_desde TEXT NOT NULL,     -- YYYY-MM-DD
    no_remunerativo TEXT NOT NULL DEFAULT '0',
    asignacion_unica TEXT NOT NULL DEFAULT '0',
    fuente TEXT,                      -- de dónde salió el monto
    verificada INTEGER NOT NULL DEFAULT 0,   -- 1 si se contrastó con el acuerdo o recibos
    UNIQUE (convenio, categoria, vigencia_desde)
);

CREATE TABLE IF NOT EXISTS empleados (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    empresa_id INTEGER NOT NULL REFERENCES empresas(id),
    legajo TEXT,
    apellido TEXT NOT NULL,
    nombre TEXT NOT NULL,
    cuil TEXT NOT NULL,
    convenio TEXT NOT NULL DEFAULT 'CCT 130/75' REFERENCES convenios(codigo),
    categoria TEXT NOT NULL,
    fecha_ingreso TEXT NOT NULL,      -- YYYY-MM-DD
    fecha_egreso TEXT,                -- NULL = activo
    jornada_horas INTEGER NOT NULL DEFAULT 8 CHECK (jornada_horas BETWEEN 1 AND 8),
    extras TEXT,                      -- JSON con datos propios del convenio (afiliado, tareas...)
    UNIQUE (empresa_id, cuil),
    UNIQUE (empresa_id, legajo)
);

CREATE TABLE IF NOT EXISTS liquidaciones (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    empleado_id INTEGER NOT NULL REFERENCES empleados(id),
    periodo TEXT NOT NULL,            -- YYYY-MM (para SAC, el mes de pago)
    tipo TEXT NOT NULL DEFAULT 'mensual' CHECK (tipo IN ('mensual', 'sac', 'final', 'zona_fria')),
    fecha_pago TEXT NOT NULL,
    lugar_pago TEXT NOT NULL,
    resultado TEXT NOT NULL,          -- JSON con conceptos y totales
    creada TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (empleado_id, periodo, tipo)
);
"""


def connect(path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)
    _migrar_tipo_final(conn)
    _migrar_extras(conn)
    _migrar_zona(conn)
    conn.commit()


def _migrar_zona(conn: sqlite3.Connection) -> None:
    """Bases creadas antes de la zona fría: agrega su base y si va en recibo aparte."""
    columnas = {r["name"] for r in conn.execute("PRAGMA table_info(edificios)")}
    if "zona_base" not in columnas:
        conn.execute("ALTER TABLE edificios ADD COLUMN zona_base TEXT NOT NULL DEFAULT 'remunerativo' "
                     "CHECK (zona_base IN ('remunerativo', 'basico_antiguedad'))")
    if "zona_recibo_aparte" not in columnas:
        conn.execute("ALTER TABLE edificios ADD COLUMN zona_recibo_aparte INTEGER NOT NULL DEFAULT 0")


def _migrar_extras(conn: sqlite3.Connection) -> None:
    """Bases creadas antes de SUTERYH: agrega empleados.extras."""
    columnas = {r["name"] for r in conn.execute("PRAGMA table_info(empleados)")}
    if "extras" not in columnas:
        conn.execute("ALTER TABLE empleados ADD COLUMN extras TEXT")


def _migrar_tipo_final(conn: sqlite3.Connection) -> None:
    """Bases viejas: amplía el CHECK de liquidaciones.tipo (liquidación final, zona fría)."""
    sql = conn.execute("SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'liquidaciones'"
                       ).fetchone()[0]
    if "'zona_fria'" in sql:
        return
    nueva = SCHEMA[SCHEMA.index("CREATE TABLE IF NOT EXISTS liquidaciones"):].split(";")[0]
    conn.execute("PRAGMA foreign_keys = OFF")
    try:
        with conn:
            conn.execute("ALTER TABLE liquidaciones RENAME TO liquidaciones_vieja")
            conn.execute(nueva)
            conn.execute("INSERT INTO liquidaciones SELECT * FROM liquidaciones_vieja")
            conn.execute("DROP TABLE liquidaciones_vieja")
    finally:
        conn.execute("PRAGMA foreign_keys = ON")


def get_db() -> sqlite3.Connection:
    if "db" not in g:
        g.db = connect(current_app.config["DATABASE"])
    return g.db


def close_db(_exc=None) -> None:
    db = g.pop("db", None)
    if db is not None:
        db.close()
