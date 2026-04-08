#!/usr/bin/env python3
"""
Тендер Dashboard Backend — Flask + PostgreSQL
Ажиллуулах: python tender_app.py
"""

import os
import psycopg2
import psycopg2.extras
import hashlib
import secrets
import json
from datetime import datetime
from functools import wraps
from flask import Flask, request, jsonify, session, send_file

app = Flask(__name__, static_folder=None)
app.secret_key = os.environ.get("SECRET_KEY", secrets.token_hex(32))

DB_CONFIG = {
    "host": os.environ.get("DB_HOST", "localhost"),
    "port": os.environ.get("DB_PORT", "5432"),
    "dbname": os.environ.get("DB_NAME", "tender_db"),
    "user": os.environ.get("DB_USER", "tender_admin"),
    "password": os.environ.get("DB_PASSWORD", "admin_pass"),
}

DASHBOARD_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "tender-dashboard.html")


# ============================================================
# DATABASE
# ============================================================

def get_db():
    conn = psycopg2.connect(**DB_CONFIG)
    conn.autocommit = False
    return conn

def query(sql, params=None, fetchone=False, fetchall=False, commit=False):
    """Нэг query ажиллуулах helper"""
    conn = get_db()
    try:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute(sql, params)
        result = None
        if fetchone:
            result = cur.fetchone()
        elif fetchall:
            result = cur.fetchall()
        if commit:
            conn.commit()
        return result
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

def init_db():
    conn = get_db()
    cur = conn.cursor()

    cur.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id SERIAL PRIMARY KEY,
            username TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            full_name TEXT NOT NULL,
            role TEXT NOT NULL DEFAULT 'viewer',
            created_at TIMESTAMP DEFAULT NOW()
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS tenders (
            id SERIAL PRIMARY KEY,
            tender_no TEXT UNIQUE NOT NULL,
            name TEXT,
            organization TEXT,
            type TEXT,
            method TEXT,
            deadline TEXT,
            electronic TEXT,
            link TEXT,
            raw_data TEXT,
            created_at TIMESTAMP DEFAULT NOW(),
            updated_at TIMESTAMP DEFAULT NOW(),
            created_by INTEGER REFERENCES users(id)
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS upload_history (
            id SERIAL PRIMARY KEY,
            user_id INTEGER NOT NULL REFERENCES users(id),
            filename TEXT,
            total_in_file INTEGER DEFAULT 0,
            inserted INTEGER DEFAULT 0,
            updated INTEGER DEFAULT 0,
            unchanged INTEGER DEFAULT 0,
            uploaded_at TIMESTAMP DEFAULT NOW()
        )
    """)

    # Index-ууд
    cur.execute("CREATE INDEX IF NOT EXISTS idx_tenders_tender_no ON tenders(tender_no)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_tenders_deadline ON tenders(deadline)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_tenders_organization ON tenders(organization)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_tenders_type ON tenders(type)")

    conn.commit()

    # Анхдагч admin
    cur.execute("SELECT id FROM users WHERE username = %s", ("admin",))
    if not cur.fetchone():
        pw_hash = hashlib.sha256("admin123".encode()).hexdigest()
        cur.execute(
            "INSERT INTO users (username, password_hash, full_name, role) VALUES (%s, %s, %s, %s)",
            ("admin", pw_hash, "Админ", "admin")
        )
        conn.commit()
        print("[tender] Анхдагч хэрэглэгч: admin / admin123")

    conn.close()


# ============================================================
# MIDDLEWARE
# ============================================================

def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if "user_id" not in session:
            return jsonify({"error": "Нэвтрэх шаардлагатай"}), 401
        return f(*args, **kwargs)
    return decorated

def editor_required(f):
    """Зөвхөн admin, editor эрхтэй хэрэглэгч"""
    @wraps(f)
    def decorated(*args, **kwargs):
        if "user_id" not in session:
            return jsonify({"error": "Нэвтрэх шаардлагатай"}), 401
        if session.get("role") not in ("admin", "editor"):
            return jsonify({"error": "Мэдээлэл оруулах эрхгүй. admin эсвэл editor эрх шаардлагатай."}), 403
        return f(*args, **kwargs)
    return decorated


# ============================================================
# AUTH API
# ============================================================

@app.route("/api/login", methods=["POST"])
def login():
    data = request.get_json()
    username = data.get("username", "").strip()
    password = data.get("password", "").strip()

    if not username or not password:
        return jsonify({"error": "Хэрэглэгчийн нэр, нууц үг оруулна уу"}), 400

    pw_hash = hashlib.sha256(password.encode()).hexdigest()
    user = query(
        "SELECT id, username, full_name, role FROM users WHERE username = %s AND password_hash = %s",
        (username, pw_hash), fetchone=True
    )

    if not user:
        return jsonify({"error": "Хэрэглэгчийн нэр эсвэл нууц үг буруу"}), 401

    session["user_id"] = user["id"]
    session["username"] = user["username"]
    session["full_name"] = user["full_name"]
    session["role"] = user["role"]

    return jsonify({
        "success": True,
        "user": {
            "username": user["username"],
            "full_name": user["full_name"],
            "role": user["role"]
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
            "username": session["username"],
            "full_name": session["full_name"],
            "role": session["role"]
        }
    })


# ============================================================
# ХЭРЭГЛЭГЧ УДИРДЛАГА (admin only)
# ============================================================

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
    username = data.get("username", "").strip()
    password = data.get("password", "").strip()
    full_name = data.get("full_name", "").strip()
    role = data.get("role", "viewer")

    if role not in ("admin", "editor", "viewer"):
        return jsonify({"error": "Эрх буруу. admin, editor, viewer сонгоно уу"}), 400
    if not username or not password or not full_name:
        return jsonify({"error": "Бүх талбарыг бөглөнө үү"}), 400

    pw_hash = hashlib.sha256(password.encode()).hexdigest()
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


# ============================================================
# ТЕНДЕР UPLOAD — diff/merge логик
# ============================================================

TENDER_FIELDS = ("name", "organization", "type", "method", "deadline", "electronic", "link")

@app.route("/api/tenders/upload", methods=["POST"])
@editor_required
def upload_tenders():
    data = request.get_json()
    tenders = data.get("tenders", [])
    filename = data.get("filename", "unknown.json")

    if not tenders or not isinstance(tenders, list):
        return jsonify({"error": "Тендерийн мэдээлэл хоосон байна"}), 400

    conn = get_db()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    user_id = session["user_id"]
    now = datetime.utcnow().isoformat()

    inserted = 0
    updated = 0
    unchanged = 0
    changes = []

    try:
        for t in tenders:
            tender_no = t.get("tender_no", "").strip()
            if not tender_no:
                continue

            cur.execute(
                "SELECT id, name, organization, type, method, deadline, electronic, link FROM tenders WHERE tender_no = %s",
                (tender_no,)
            )
            existing = cur.fetchone()

            if existing is None:
                cur.execute("""
                    INSERT INTO tenders (tender_no, name, organization, type, method, deadline, electronic, link, raw_data, created_by, created_at, updated_at)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """, (
                    tender_no,
                    t.get("name"), t.get("organization"), t.get("type"),
                    t.get("method"), t.get("deadline"), t.get("electronic"), t.get("link"),
                    json.dumps(t, ensure_ascii=False),
                    user_id, now, now
                ))
                inserted += 1
                changes.append({"tender_no": tender_no, "action": "new", "name": t.get("name", "")})
            else:
                diffs = {}
                for field in TENDER_FIELDS:
                    old_val = existing[field] or ""
                    new_val = t.get(field, "") or ""
                    if str(old_val) != str(new_val):
                        diffs[field] = {"old": old_val, "new": new_val}

                if diffs:
                    cur.execute("""
                        UPDATE tenders
                        SET name=%s, organization=%s, type=%s, method=%s, deadline=%s, electronic=%s, link=%s, raw_data=%s, updated_at=%s
                        WHERE tender_no=%s
                    """, (
                        t.get("name"), t.get("organization"), t.get("type"),
                        t.get("method"), t.get("deadline"), t.get("electronic"), t.get("link"),
                        json.dumps(t, ensure_ascii=False),
                        now, tender_no
                    ))
                    updated += 1
                    changes.append({"tender_no": tender_no, "action": "updated", "name": t.get("name", ""), "diffs": diffs})
                else:
                    unchanged += 1

        cur.execute("""
            INSERT INTO upload_history (user_id, filename, total_in_file, inserted, updated, unchanged)
            VALUES (%s, %s, %s, %s, %s, %s)
        """, (user_id, filename, len(tenders), inserted, updated, unchanged))

        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

    return jsonify({
        "success": True,
        "summary": {
            "total_in_file": len(tenders),
            "inserted": inserted,
            "updated": updated,
            "unchanged": unchanged
        },
        "changes": changes[:50]
    })


# ============================================================
# ТЕНДЕР QUERY API
# ============================================================

@app.route("/api/tenders")
@login_required
def get_tenders():
    total = query("SELECT COUNT(*) as cnt FROM tenders", fetchone=True)["cnt"]

    limit = min(int(request.args.get("limit", 5000)), 10000)
    offset = int(request.args.get("offset", 0))

    rows = query("""
        SELECT tender_no, name, organization, type, method, deadline, electronic, link
        FROM tenders ORDER BY deadline ASC LIMIT %s OFFSET %s
    """, (limit, offset), fetchall=True)

    return jsonify({
        "total": total,
        "tenders": rows
    })

@app.route("/api/tenders/stats")
@login_required
def tender_stats():
    total = query("SELECT COUNT(*) as cnt FROM tenders", fetchone=True)["cnt"]
    orgs = query("SELECT COUNT(DISTINCT organization) as cnt FROM tenders", fetchone=True)["cnt"]
    types = query("SELECT type, COUNT(*) as cnt FROM tenders GROUP BY type", fetchall=True)
    methods = query("SELECT method, COUNT(*) as cnt FROM tenders GROUP BY method", fetchall=True)
    return jsonify({
        "total": total,
        "organizations": orgs,
        "types": types,
        "methods": methods
    })

@app.route("/api/tenders/history")
@login_required
def upload_history():
    rows = query("""
        SELECT h.*, u.full_name, u.username
        FROM upload_history h JOIN users u ON h.user_id = u.id
        ORDER BY h.uploaded_at DESC LIMIT 50
    """, fetchall=True)
    return jsonify(rows)


# ============================================================
# DASHBOARD PAGE
# ============================================================

@app.route("/")
def index():
    return send_file(DASHBOARD_PATH)


# ============================================================
# MAIN
# ============================================================

if __name__ == "__main__":
    init_db()
    port = int(os.environ.get("PORT", 5000))
    print(f"[tender] PostgreSQL: {DB_CONFIG['host']}:{DB_CONFIG['port']}/{DB_CONFIG['dbname']}")
    print(f"[tender] Сервер эхэллээ: http://localhost:{port}")
    print(f"[tender] Анхдагч нэвтрэх: admin / admin123")
    print(f"[tender] Эрхүүд: admin (бүх эрх), editor (мэдээлэл оруулах), viewer (зөвхөн харах)")
    app.run(host="0.0.0.0", port=port, debug=True)
