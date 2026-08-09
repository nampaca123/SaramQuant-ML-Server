class APIError(Exception):
    def __init__(self, message: str, status_code: int = 400):
        self.message = message
        self.status_code = status_code


class NotFoundError(APIError):
    def __init__(self, resource: str):
        super().__init__(f"{resource} not found", 404)


class InsufficientDataError(APIError):
    def __init__(self, required: int, actual: int):
        super().__init__(f"Insufficient data: {actual}/{required} days", 400)


def register_error_handlers(app):
    # 배치 이미지에는 flask가 없으므로 API 경로에서 호출될 때만 import한다.
    from flask import jsonify

    @app.errorhandler(APIError)
    def handle_api_error(error):
        return jsonify({"error": error.message}), error.status_code

    @app.errorhandler(404)
    def handle_not_found(error):
        return jsonify({"error": "Resource not found"}), 404

    @app.errorhandler(500)
    def handle_internal_error(error):
        return jsonify({"error": "Internal server error"}), 500
