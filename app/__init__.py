from flask import Flask
from app.utils.system.errors import register_error_handlers


def create_app() -> Flask:
    app = Flask(__name__)

    register_error_handlers(app)

    from app.api import api_bp
    from app.api.quant.simulation import simulation_bp

    app.register_blueprint(api_bp)
    app.register_blueprint(simulation_bp)

    @app.route("/health")
    def health():
        return {"status": "ok"}

    return app