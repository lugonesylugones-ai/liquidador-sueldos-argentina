import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent


def _sqlite_path(url: str) -> str:
    """Convierte 'sqlite:///./archivo.db' en una ruta de archivo."""
    prefijo = "sqlite:///"
    if not url.startswith(prefijo):
        raise ValueError(f"Solo se soporta SQLite, se recibió: {url}")
    ruta = url[len(prefijo):]
    if ruta == ":memory:":
        return ruta
    ruta = Path(ruta)
    if not ruta.is_absolute():
        ruta = BASE_DIR / ruta
    return str(ruta)


class Config:
    SECRET_KEY = os.environ.get("SECRET_KEY", "dev")
    DATABASE = _sqlite_path(os.environ.get("DATABASE_URL", "sqlite:///./liquidador_sueldos.db"))
    UPLOAD_FOLDER = str(BASE_DIR / os.environ.get("UPLOAD_FOLDER", "uploads/"))
    REPORTS_FOLDER = str(BASE_DIR / os.environ.get("REPORTS_FOLDER", "reports/"))
    MAX_CONTENT_LENGTH = int(os.environ.get("MAX_CONTENT_LENGTH", 16 * 1024 * 1024))
