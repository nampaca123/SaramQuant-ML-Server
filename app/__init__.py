import hmac
import os

from flask import Flask, request

from app.utils.system.errors import register_error_handlers

_CALC_AUTH_KEY = os.getenv("CALC_AUTH_KEY", "")


def create_app() -> Flask:
    app = Flask(__name__)

    register_error_handlers(app)

    from app.api import api_bp
    from app.api.quant.simulation import simulation_bp

    app.register_blueprint(api_bp)
    app.register_blueprint(simulation_bp)

    @app.before_request
    def _check_api_key():
        if request.path.startswith("/internal"):
            provided = request.headers.get("x-api-key", "")
            if not hmac.compare_digest(provided, _CALC_AUTH_KEY):
                return {"error": "Unauthorized"}, 401

    @app.route("/health")
    def health():
        return {"status": "ok"}

    return app