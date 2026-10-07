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

-- Códigos del empleador para el Libro de Sueldos Digital / F.931 de ARCA, 1 a 1 con la empresa.
CREATE TABLE IF NOT EXISTS datos_arca (
    empresa_id INTEGER PRIMARY KEY REFERENCES empresas(id),
    tipo_empleador TEXT NOT NULL,     -- tabla "Tipos de empleador" de Declaración en Línea
    actividad TEXT NOT NULL,          -- tabla "Actividades"
    zona TEXT NOT NULL,               -- tabla "Localidades / zonas"
    codigos TEXT                      -- JSON: código del liquidador -> código de concepto del empleador
);

-- Períodos ya presentados (F.931): no se pueden volver a liquidar sin reabrirlos.
CREATE TABLE IF NOT EXISTS periodos_cerrados (
    empresa_id INTEGER NOT NULL REFERENCES empresas(id),
    periodo TEXT NOT NULL,            -- YYYY-MM
    cerrado TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (empresa_id, periodo)
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

-- Conceptos que carga el usuario (plus, premios, sumas no remunerativas, descuentos) y su fórmula.
CREATE TABLE IF NOT EXISTS conceptos (
    codigo TEXT PRIMARY KEY,          -- va en el recibo y en el Libro de Sueldos Digital
    descripcion TEXT NOT NULL,
    tipo TEXT NOT NULL CHECK (tipo IN ('remunerativo', 'no_remunerativo', 'descuento')),
    calculo TEXT NOT NULL CHECK (calculo IN ('fijo', 'porcentaje', 'cantidad', 'formula')),
    valor TEXT NOT NULL DEFAULT '0',  -- importe, porcentaje o valor unitario (Decimal como texto)
    base TEXT NOT NULL DEFAULT '',    -- porcentaje: códigos que suma, separados por coma
    formula TEXT NOT NULL DEFAULT '',
    proporcional INTEGER NOT NULL DEFAULT 1,   -- suma fija: × horas/8 × días/30
    habitual INTEGER NOT NULL DEFAULT 1,       -- cuenta para SAC, vacaciones e indemnizaciones
    aportes INTEGER NOT NULL DEFAULT 1,        -- no remunerativo: paga obra social y aportes sindicales
    arca TEXT NOT NULL,               -- código de concepto ARCA (6 dígitos)
    convenio TEXT REFERENCES convenios(codigo),   -- NULL: cualquier convenio
    empresa_id INTEGER REFERENCES empresas(id),   -- NULL: todas las empresas
    automatico INTEGER NOT NULL DEFAULT 0,     -- 1: a todos los del convenio/empresa; 0: a quien se le asigne
    orden INTEGER NOT NULL DEFAULT 100,        -- se calculan de menor a mayor
    activo INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS liquidaciones (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    empleado_id INTEGER NOT NULL REFERENCES empleados(id),
    periodo TEXT NOT NULL,            -- YYYY-MM (para SAC, el mes de pago)
    tipo TEXT NOT NULL DEFAULT 'mensual' CHECK (tipo IN ('mensual', 'sac', 'final', 'zona_fria', 'sac_zona_fria')),
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
    _migrar_codigos_arca(conn)
    conn.commit()


def _migrar_zona(conn: sqlite3.Connection) -> None:
    """Bases creadas antes de la zona fría: agrega su base y si va en recibo aparte."""
    columnas = {r["name"] for r in conn.execute("PRAGMA table_info(edificios)")}
    if "zona_base" not in columnas:
        conn.execute("ALTER TABLE edificios ADD COLUMN zona_base TEXT NOT NULL DEFAULT 'remunerativo' "
                     "CHECK (zona_base IN ('remunerativo', 'basico_antiguedad'))")
    if "zona_recibo_aparte" not in columnas:
        conn.execute("ALTER TABLE edificios ADD COLUMN zona_recibo_aparte INTEGER NOT NULL DEFAULT 0")


def _migrar_codigos_arca(conn: sqlite3.Connection) -> None:
    """Bases creadas antes de los códigos de concepto del contador."""
    if "codigos" not in {r["name"] for r in conn.execute("PRAGMA table_info(datos_arca)")}:
        conn.execute("ALTER TABLE datos_arca ADD COLUMN codigos TEXT")


def _migrar_extras(conn: sqlite3.Connection) -> None:
    """Bases creadas antes de SUTERYH: agrega empleados.extras."""
    columnas = {r["name"] for r in conn.execute("PRAGMA table_info(empleados)")}
    if "extras" not in columnas:
        conn.execute("ALTER TABLE empleados ADD COLUMN extras TEXT")


def _migrar_tipo_final(conn: sqlite3.Connection) -> None:
    """Bases viejas: amplía el CHECK de liquidaciones.tipo (liquidación final, zona fría)."""
    sql = conn.execute("SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'liquidaciones'"
                       ).fetchone()[0]
    if "'sac_zona_fria'" in sql:
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


def copia_de_seguridad(origen: str, carpeta: str, conservar: int = 30) -> str | None:
    """Copia la base a `carpeta` (una por día, con la fecha en el nombre) y borra las más viejas.

    Usa la API de backup de SQLite, que copia bien aunque la base esté abierta.
    """
    from datetime import date
    from pathlib import Path
    if origen == ":memory:" or not Path(origen).exists():
        return None
    destino_dir = Path(carpeta)
    destino_dir.mkdir(parents=True, exist_ok=True)
    destino = destino_dir / f"{Path(origen).stem}_{date.today().isoformat()}.db"
    if not destino.exists():
        with sqlite3.connect(origen) as src, sqlite3.connect(destino) as dst:
            src.backup(dst)
        dst.close()
        src.close()
    copias = sorted(destino_dir.glob(f"{Path(origen).stem}_*.db"))
    for vieja in copias[:-conservar]:
        vieja.unlink()
    return str(destino)
