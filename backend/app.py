"""Aplicación Flask del liquidador.

Soporta varias empresas, cada una con sus empleados y cada empleado con su
convenio. Tienen motor de cálculo Comercio (CCT 130/75) y edificios
(CCT 589/10); los demás se pueden dar de alta pero no liquidar.

Las pantallas están en `web` (raíz del sitio) y la API JSON en `api` (/api).
"""
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

    app.register_blueprint(api.bp)
    app.register_blueprint(web.bp)
    return app
