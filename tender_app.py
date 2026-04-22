#!/usr/bin/env python3
"""
Тендер Dashboard Backend — Flask + PostgreSQL
Ажиллуулах: python tender_app.py
"""

import os
import secrets

from flask import Flask

from config import DB_CONFIG
from extensions import limiter
from db import init_db

import auth
import users
import tenders
import procurement
import joined
import embeddings
import ocr
import ai
import admin


def create_app() -> Flask:
    app = Flask(__name__, static_folder=None)
    app.secret_key = os.environ.get("SECRET_KEY", secrets.token_hex(32))

    limiter.init_app(app)

    auth.register(app)
    users.register(app)
    tenders.register(app)
    procurement.register(app)
    joined.register(app)
    embeddings.register(app)
    ocr.register(app)
    ai.register(app)
    admin.register(app)

    return app


if __name__ == "__main__":
    app = create_app()
    init_db()

    port  = int(os.environ.get("PORT", 5000))
    debug = os.environ.get("FLASK_DEBUG", "false").lower() == "true"

    print(f"[tender] PostgreSQL: {DB_CONFIG['host']}:{DB_CONFIG['port']}/{DB_CONFIG['dbname']}")
    print(f"[tender] Сервер эхэллээ: http://localhost:{port}")
    print(f"[tender] Анхдагч нэвтрэх: admin / $ADMIN_PASSWORD")
    print(f"[tender] Эрхүүд: admin (бүх эрх), editor (мэдээлэл оруулах), viewer (зөвхөн харах)")

    app.run(host="0.0.0.0", port=port, debug=debug)
