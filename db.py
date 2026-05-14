import os
import logging
import psycopg2
import psycopg2.extras
import psycopg2.pool
from werkzeug.security import generate_password_hash

from config import DB_CONFIG

_pool: psycopg2.pool.ThreadedConnectionPool | None = None


def get_pool() -> psycopg2.pool.ThreadedConnectionPool:
    global _pool
    if _pool is None:
        _pool = psycopg2.pool.ThreadedConnectionPool(2, 20, **DB_CONFIG)
    return _pool


def get_db():
    conn = get_pool().getconn()
    conn.autocommit = False
    return conn


def release_db(conn):
    get_pool().putconn(conn)


def query(sql, params=None, fetchone=False, fetchall=False, commit=False):
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
        release_db(conn)


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
            name TEXT, organization TEXT, type TEXT, method TEXT,
            deadline TEXT, electronic TEXT, link TEXT, raw_data TEXT,
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
    cur.execute("""
        CREATE TABLE IF NOT EXISTS procurement_plans (
            id SERIAL PRIMARY KEY,
            row_num INTEGER, ts_code TEXT, name TEXT, ts_type TEXT,
            funding_source TEXT, budget_amount NUMERIC(18,2) DEFAULT 0,
            procurement_method TEXT, tender_announce_date TEXT,
            contract_award_date TEXT, contract_end_date TEXT,
            annual_funding NUMERIC(18,2) DEFAULT 0,
            sustainable_criteria TEXT, description TEXT, created_date TEXT,
            sub_category TEXT, organization TEXT, plan_year INTEGER,
            created_by INTEGER REFERENCES users(id),
            created_at TIMESTAMP DEFAULT NOW(),
            updated_at TIMESTAMP DEFAULT NOW()
        )
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS procurement_upload_history (
            id SERIAL PRIMARY KEY,
            user_id INTEGER NOT NULL REFERENCES users(id),
            filename TEXT, organization TEXT, plan_year INTEGER,
            total_rows INTEGER DEFAULT 0, inserted INTEGER DEFAULT 0,
            updated INTEGER DEFAULT 0,
            uploaded_at TIMESTAMP DEFAULT NOW()
        )
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS joined_tenders (
            id SERIAL PRIMARY KEY,
            tender_no TEXT, code TEXT, name TEXT, organization TEXT,
            tender_type TEXT, method TEXT, status TEXT, deadline TEXT,
            link TEXT,
            created_by INTEGER REFERENCES users(id),
            created_at TIMESTAMP DEFAULT NOW()
        )
    """)
    cur.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_joined_tender_no ON joined_tenders(tender_no)")
    cur.execute("""
        CREATE TABLE IF NOT EXISTS tender_details (
            id SERIAL PRIMARY KEY,
            tender_no TEXT UNIQUE NOT NULL,
            requirements TEXT, fail_reason TEXT, notes TEXT, ocr_text TEXT,
            updated_by INTEGER REFERENCES users(id),
            updated_at TIMESTAMP DEFAULT NOW()
        )
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS profile_keywords (
            id SERIAL PRIMARY KEY,
            keyword TEXT UNIQUE NOT NULL,
            weight NUMERIC(4,2) DEFAULT 1.0,
            category TEXT,
            created_at TIMESTAMP DEFAULT NOW()
        )
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS won_tenders (
            id SERIAL PRIMARY KEY,
            tender_no TEXT UNIQUE, code TEXT, name TEXT, organization TEXT,
            tender_type TEXT, method TEXT, status TEXT, deadline TEXT, link TEXT,
            created_by INTEGER REFERENCES users(id),
            created_at TIMESTAMP DEFAULT NOW()
        )
    """)
    cur.execute("CREATE EXTENSION IF NOT EXISTS vector")
    cur.execute("""
        CREATE TABLE IF NOT EXISTS tender_embeddings (
            id SERIAL PRIMARY KEY,
            tender_no TEXT NOT NULL,
            chunk_index INTEGER DEFAULT 0,
            chunk_text TEXT,
            embedding vector(1536),
            source TEXT DEFAULT 'ocr',
            created_at TIMESTAMP DEFAULT NOW()
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS sso_tokens (
            token TEXT PRIMARY KEY,
            user_id INTEGER NOT NULL REFERENCES users(id),
            username TEXT NOT NULL,
            full_name TEXT NOT NULL,
            role TEXT NOT NULL,
            expires_at TIMESTAMP NOT NULL,
            created_at TIMESTAMP DEFAULT NOW()
        )
    """)

    for idx_sql in [
        "CREATE INDEX IF NOT EXISTS idx_emb_tender ON tender_embeddings(tender_no)",
        "CREATE INDEX IF NOT EXISTS idx_tenders_tender_no ON tenders(tender_no)",
        "CREATE INDEX IF NOT EXISTS idx_tenders_deadline ON tenders(deadline)",
        "CREATE INDEX IF NOT EXISTS idx_tenders_organization ON tenders(organization)",
        "CREATE INDEX IF NOT EXISTS idx_tenders_type ON tenders(type)",
        "CREATE INDEX IF NOT EXISTS idx_procurement_ts_code ON procurement_plans(ts_code)",
        "CREATE INDEX IF NOT EXISTS idx_procurement_org ON procurement_plans(organization)",
        "CREATE INDEX IF NOT EXISTS idx_procurement_type ON procurement_plans(ts_type)",
        "CREATE INDEX IF NOT EXISTS idx_procurement_year ON procurement_plans(plan_year)",
        "CREATE INDEX IF NOT EXISTS idx_tenders_name_lower ON tenders(LOWER(TRIM(name)))",
        "CREATE INDEX IF NOT EXISTS idx_procurement_name_lower ON procurement_plans(LOWER(TRIM(name)))",
    ]:
        cur.execute(idx_sql)

    conn.commit()

    cur.execute("SELECT id FROM users WHERE username = %s", ("admin",))
    if not cur.fetchone():
        default_pw = os.environ.get("ADMIN_PASSWORD", "admin123")
        pw_hash = generate_password_hash(default_pw)
        cur.execute(
            "INSERT INTO users (username, password_hash, full_name, role) VALUES (%s, %s, %s, %s)",
            ("admin", pw_hash, "Админ", "admin")
        )
        conn.commit()
        if default_pw == "admin123":
            logging.warning("АНХААРУУЛГА: Анхдагч нууц үг ашиглагдаж байна. ADMIN_PASSWORD env var тохируулна уу.")
        print(f"[tender] Анхдагч хэрэглэгч: admin / {default_pw}")

    release_db(conn)
