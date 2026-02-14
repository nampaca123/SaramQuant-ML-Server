from flask import Blueprint

API_PREFIX = "/api"

api_bp = Blueprint("api", __name__, url_prefix=API_PREFIX)