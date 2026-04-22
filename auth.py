from functools import wraps
from flask import request, jsonify, session
from werkzeug.security import check_password_hash

from db import query
from extensions import limiter


def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if "user_id" not in session:
            return jsonify({"error": "Нэвтрэх шаардлагатай"}), 401
        return f(*args, **kwargs)
    return decorated


def editor_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if "user_id" not in session:
            return jsonify({"error": "Нэвтрэх шаардлагатай"}), 401
        if session.get("role") not in ("admin", "editor"):
            return jsonify({"error": "Мэдээлэл оруулах эрхгүй. admin эсвэл editor эрх шаардлагатай."}), 403
        return f(*args, **kwargs)
    return decorated


def register(app):
    @app.route("/api/login", methods=["POST"])
    @limiter.limit("10 per minute")
    def login():
        data = request.get_json()
        username = data.get("username", "").strip()
        password = data.get("password", "").strip()

        if not username or not password:
            return jsonify({"error": "Хэрэглэгчийн нэр, нууц үг оруулна уу"}), 400

        user = query(
            "SELECT id, username, full_name, role, password_hash FROM users WHERE username = %s",
            (username,), fetchone=True
        )

        if not user or not check_password_hash(user["password_hash"], password):
            return jsonify({"error": "Хэрэглэгчийн нэр эсвэл нууц үг буруу"}), 401

        session["user_id"]   = user["id"]
        session["username"]  = user["username"]
        session["full_name"] = user["full_name"]
        session["role"]      = user["role"]

        return jsonify({
            "success": True,
            "user": {
                "username":  user["username"],
                "full_name": user["full_name"],
                "role":      user["role"]
            }
        })

    @app.route("/api/logout", methods=["POST"])
    def logout():
        session.clear()
        return jsonify({"success": True})

    @app.route("/api/me")
    def me():
        if "user_id" not in session:
            return jsonify({"logged_in": False}), 401
        return jsonify({
            "logged_in": True,
            "user": {
                "username":  session["username"],
                "full_name": session["full_name"],
                "role":      session["role"]
            }
        })
