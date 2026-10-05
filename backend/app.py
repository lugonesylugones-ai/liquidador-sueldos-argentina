"""Aplicación Flask del liquidador.

Soporta varias empresas, cada una con sus empleados y cada empleado con su
convenio. Tienen motor de cálculo Comercio (CCT 130/75) y edificios
(CCT 589/10); los demás se pueden dar de alta pero no liquidar.

Las pantallas están en `web` (raíz del sitio) y la API JSON en `api` (/api).
"""
from pathlib import Path

from flask import Flask

from . import api, web
from . import db as dbmod
from .config import Config
from .datos_iniciales import cargar_escalas_iniciales, cargar_estructura
from .formato import pesos


def create_app(config: dict | None = None) -> Flask:
    app = Flask(__name__)
    app.config.from_object(Config)
    if config:
        app.config.update(config)
    app.teardown_appcontext(dbmod.close_db)
    app.jinja_env.filters["pesos"] = pesos

    with app.app_context():
        conn = dbmod.get_db()
        dbmod.init_schema(conn)
        cargar_estructura(conn)
        if app.config.get("CARGAR_ESCALAS_INICIALES", True):
            cargar_escalas_iniciales(conn)

    def copia_diaria():
        carpeta = app.config.get("CARPETA_COPIAS") or str(Path(app.config["DATABASE"]).parent / "copias")
        try:
            dbmod.copia_de_seguridad(app.config["DATABASE"], carpeta)
        except OSError as exc:   # una copia que falla no tiene que frenar el trabajo
            app.logger.warning("No se pudo hacer la copia de seguridad: %s", exc)

    copia_diaria()
    app.before_request(copia_diaria)

    app.register_blueprint(api.bp)
    app.register_blueprint(web.bp)
    return app
