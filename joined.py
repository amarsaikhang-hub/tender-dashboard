from flask import request, jsonify, session

from db import get_db, release_db, query
from auth import login_required, editor_required


def register(app):
    @app.route("/api/joined-tenders/upload", methods=["POST"])
    @editor_required
    def upload_joined_tenders():
        data    = request.get_json()
        tenders = data.get("tenders", [])
        if not tenders or not isinstance(tenders, list):
            return jsonify({"error": "Тендерийн мэдээлэл хоосон"}), 400

        conn    = get_db()
        cur     = conn.cursor()
        user_id = session["user_id"]
        inserted = updated = 0

        try:
            for t in tenders:
                tender_no = (t.get("tender_no") or "").strip()
                if not tender_no:
                    continue

                name        = (t.get("name")         or "").strip()
                org         = (t.get("organization")  or "").strip()
                code        = (t.get("code")          or "").strip()
                tender_type = (t.get("electronic") or t.get("type")   or "").strip()
                method_val  = (t.get("type")       or t.get("method") or "").strip()
                status_val  = (t.get("method")        or "").strip()
                deadline    = (t.get("deadline")      or "").strip()
                if deadline == "null":
                    deadline = ""
                link = (t.get("link") or "").strip()

                cur.execute("SELECT id FROM joined_tenders WHERE tender_no = %s", (tender_no,))
                if cur.fetchone():
                    cur.execute("""
                        UPDATE joined_tenders
                        SET code=%s, name=%s, organization=%s, tender_type=%s,
                            method=%s, status=%s, deadline=%s, link=%s
                        WHERE tender_no=%s
                    """, (code, name, org, tender_type, method_val, status_val, deadline, link, tender_no))
                    updated += 1
                else:
                    cur.execute("""
                        INSERT INTO joined_tenders
                            (tender_no, code, name, organization, tender_type,
                             method, status, deadline, link, created_by)
                        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                    """, (tender_no, code, name, org, tender_type, method_val, status_val,
                          deadline, link, user_id))
                    inserted += 1

            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            release_db(conn)

        return jsonify({"success": True, "summary": {"total": len(tenders),
                        "inserted": inserted, "updated": updated}})

    @app.route("/api/won-tenders/upload", methods=["POST"])
    @editor_required
    def upload_won_tenders():
        data    = request.get_json()
        tenders = data.get("tenders", [])
        if not tenders or not isinstance(tenders, list):
            return jsonify({"error": "Тендерийн мэдээлэл хоосон"}), 400

        conn    = get_db()
        cur     = conn.cursor()
        user_id = session["user_id"]
        inserted = updated = 0

        try:
            for t in tenders:
                tender_no = (t.get("tender_no") or "").strip()
                if not tender_no:
                    continue

                code        = (t.get("code")          or "").strip()
                name        = (t.get("name")          or "").strip()
                org         = (t.get("organization")  or "").strip()
                tender_type = (t.get("electronic") or t.get("type")   or "").strip()
                method_val  = (t.get("type")       or t.get("method") or "").strip()
                status_val  = (t.get("method")        or "").strip()
                deadline    = (t.get("deadline")      or "").strip()
                if deadline == "null":
                    deadline = ""
                link = (t.get("link") or "").strip()

                cur.execute("SELECT id FROM won_tenders WHERE tender_no = %s", (tender_no,))
                if cur.fetchone():
                    cur.execute("""
                        UPDATE won_tenders
                        SET code=%s, name=%s, organization=%s, tender_type=%s,
                            method=%s, status=%s, deadline=%s, link=%s
                        WHERE tender_no=%s
                    """, (code, name, org, tender_type, method_val, status_val, deadline, link, tender_no))
                    updated += 1
                else:
                    cur.execute("""
                        INSERT INTO won_tenders
                            (tender_no, code, name, organization, tender_type,
                             method, status, deadline, link, created_by)
                        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                    """, (tender_no, code, name, org, tender_type, method_val, status_val,
                          deadline, link, user_id))
                    inserted += 1

            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            release_db(conn)

        return jsonify({"success": True, "summary": {"total": len(tenders),
                        "inserted": inserted, "updated": updated}})

    @app.route("/api/joined-tenders")
    @login_required
    def get_joined_tenders():
        search = request.args.get("search", "")
        status = request.args.get("status", "")
        t_type = request.args.get("type", "")
        won    = request.args.get("won", "")
        limit  = min(int(request.args.get("limit", 100)), 5000)
        offset = int(request.args.get("offset", 0))

        where, params = ["1=1"], []
        if search:
            where.append("(j.name ILIKE %s OR j.tender_no ILIKE %s OR j.organization ILIKE %s)")
            params.extend([f"%{search}%"] * 3)
        if status: where.append("j.status = %s");      params.append(status)
        if t_type: where.append("j.tender_type = %s"); params.append(t_type)
        if won == "yes": where.append("w.tender_no IS NOT NULL")
        elif won == "no": where.append("w.tender_no IS NULL")

        where_sql = " AND ".join(where)
        total = query(f"""
            SELECT COUNT(*) as cnt FROM joined_tenders j
            LEFT JOIN won_tenders w ON j.tender_no = w.tender_no
            WHERE {where_sql}
        """, params, fetchone=True)["cnt"]

        rows = query(f"""
            SELECT j.*,
                   CASE WHEN w.tender_no IS NOT NULL THEN true ELSE false END as is_won,
                   CASE WHEN d.tender_no IS NOT NULL THEN true ELSE false END as has_details,
                   d.fail_reason, d.notes
            FROM joined_tenders j
            LEFT JOIN won_tenders w ON j.tender_no = w.tender_no
            LEFT JOIN tender_details d ON j.tender_no = d.tender_no
            WHERE {where_sql}
            ORDER BY j.id DESC LIMIT %s OFFSET %s
        """, params + [limit, offset], fetchall=True)

        return jsonify({"total": total, "tenders": rows})

    @app.route("/api/joined-tenders/stats")
    @login_required
    def joined_tender_stats():
        stats = query("""
            SELECT COUNT(*) as total,
                   COUNT(DISTINCT j.organization) as orgs,
                   COUNT(CASE WHEN j.status='Үр дүн гарсан' THEN 1 END) as completed,
                   COUNT(CASE WHEN j.status='Нээгдсэн' THEN 1 END) as opened,
                   COUNT(CASE WHEN w.tender_no IS NOT NULL THEN 1 END) as won,
                   COUNT(CASE WHEN j.status='Үр дүн гарсан' AND w.tender_no IS NULL THEN 1 END) as lost
            FROM joined_tenders j LEFT JOIN won_tenders w ON j.tender_no = w.tender_no
        """, fetchone=True)
        won_count = query("SELECT COUNT(*) as cnt FROM won_tenders", fetchone=True)["cnt"]
        return jsonify({"stats": stats, "won_total": won_count})

    @app.route("/api/tender-details/<path:tender_no>")
    @login_required
    def get_tender_detail(tender_no):
        row = query("SELECT * FROM tender_details WHERE tender_no = %s", (tender_no,), fetchone=True)
        return jsonify(row or {"tender_no": tender_no, "requirements": "",
                               "fail_reason": "", "notes": "", "ocr_text": ""})

    @app.route("/api/tender-details/<path:tender_no>", methods=["PUT"])
    @login_required
    def save_tender_detail(tender_no):
        data         = request.get_json()
        requirements = data.get("requirements", "")
        fail_reason  = data.get("fail_reason",  "")
        notes        = data.get("notes",        "")
        ocr_text     = data.get("ocr_text",     "")
        user_id      = session["user_id"]

        conn = get_db()
        cur  = conn.cursor()
        cur.execute("SELECT id FROM tender_details WHERE tender_no = %s", (tender_no,))
        if cur.fetchone():
            cur.execute("""
                UPDATE tender_details
                SET requirements=%s, fail_reason=%s, notes=%s, ocr_text=%s,
                    updated_by=%s, updated_at=NOW()
                WHERE tender_no=%s
            """, (requirements, fail_reason, notes, ocr_text, user_id, tender_no))
        else:
            cur.execute("""
                INSERT INTO tender_details (tender_no, requirements, fail_reason, notes, ocr_text, updated_by)
                VALUES (%s,%s,%s,%s,%s,%s)
            """, (tender_no, requirements, fail_reason, notes, ocr_text, user_id))
        conn.commit()
        release_db(conn)
        return jsonify({"success": True})
