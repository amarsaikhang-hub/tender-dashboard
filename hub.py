
import secrets
import urllib.request
from pathlib import Path
from datetime import datetime, timedelta
from flask import send_file, jsonify, session, redirect, request

from db import query
from auth import login_required


def register(app):

    @app.route("/hub")
    def hub_page():
        return send_file(Path(__file__).parent / "hub.html")

    @app.route("/sso")
    def sso_login():
        token = request.args.get("token", "")
        if not token:
            return redirect("/hub")
        row = query("SELECT * FROM sso_tokens WHERE token = %s AND expires_at > NOW()",
                    (token,), fetchone=True)
        if not row:
            return redirect("/hub")
        session["user_id"]  = row["user_id"]
        session["username"] = row["username"]
        session["full_name"]= row["full_name"]
        session["role"]     = row["role"]
        query("DELETE FROM sso_tokens WHERE token = %s", (token,), commit=True)
        return redirect("/hub")

    @app.route("/api/sso/token", methods=["POST"])
    @login_required
    def generate_sso_token():
        token = secrets.token_urlsafe(32)
        expires = datetime.now() + timedelta(minutes=2)
        query("""
            INSERT INTO sso_tokens (token, user_id, username, full_name, role, expires_at)
            VALUES (%s, %s, %s, %s, %s, %s)
        """, (token, session["user_id"], session["username"],
              session["full_name"], session["role"], expires), commit=True)
        # Хуучин token-уудыг цэвэрлэх
        query("DELETE FROM sso_tokens WHERE expires_at < NOW()", commit=True)
        return jsonify({"token": token})

    @app.route("/api/hub/status")
    def hub_status():
        services = [
            {"name": "Tender Dashboard",   "url": "http://localhost:5000/", "port": 5000},
            {"name": "Company Resources",  "url": "http://localhost:5001/", "port": 5001},
            {"name": "Price Database",     "url": "http://localhost:5002/", "port": 5002},
        ]
        result = []
        for s in services:
            try:
                urllib.request.urlopen(s["url"], timeout=2)
                up = True
            except Exception:
                up = False
            result.append({"name": s["name"], "port": s["port"], "up": up})
        return jsonify(result)
