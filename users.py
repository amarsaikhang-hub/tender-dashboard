import psycopg2.errors
from flask import request, jsonify, session
from werkzeug.security import generate_password_hash

from db import query
from auth import login_required


def register(app):
    @app.route("/api/users", methods=["GET"])
    @login_required
    def list_users():
        if session.get("role") != "admin":
            return jsonify({"error": "Зөвхөн админ"}), 403
        users = query("SELECT id, username, full_name, role, created_at FROM users", fetchall=True)
        return jsonify(users)

    @app.route("/api/users", methods=["POST"])
    @login_required
    def create_user():
        if session.get("role") != "admin":
            return jsonify({"error": "Зөвхөн админ"}), 403

        data = request.get_json()
        username  = data.get("username", "").strip()
        password  = data.get("password", "").strip()
        full_name = data.get("full_name", "").strip()
        role      = data.get("role", "viewer")

        if role not in ("admin", "editor", "viewer"):
            return jsonify({"error": "Эрх буруу. admin, editor, viewer сонгоно уу"}), 400
        if not username or not password or not full_name:
            return jsonify({"error": "Бүх талбарыг бөглөнө үү"}), 400

        pw_hash = generate_password_hash(password)
        try:
            query(
                "INSERT INTO users (username, password_hash, full_name, role) VALUES (%s, %s, %s, %s)",
                (username, pw_hash, full_name, role), commit=True
            )
        except psycopg2.errors.UniqueViolation:
            return jsonify({"error": "Хэрэглэгчийн нэр давхцаж байна"}), 409
        return jsonify({"success": True}), 201

    @app.route("/api/users/<int:user_id>", methods=["DELETE"])
    @login_required
    def delete_user(user_id):
        if session.get("role") != "admin":
            return jsonify({"error": "Зөвхөн админ"}), 403
        if user_id == session.get("user_id"):
            return jsonify({"error": "Өөрийгөө устгах боломжгүй"}), 400
        query("DELETE FROM users WHERE id = %s", (user_id,), commit=True)
        return jsonify({"success": True})
