import json
import logging
from datetime import datetime
from decimal import Decimal

import psycopg2.extras
from flask import request, jsonify, session

from db import get_db, release_db, query
from auth import login_required, editor_required

TENDER_FIELDS = ("name", "organization", "type", "method", "deadline", "electronic", "link")


def register(app):
    @app.route("/api/tenders/upload", methods=["POST"])
    @editor_required
    def upload_tenders():
        data     = request.get_json()
        tenders  = data.get("tenders", [])
        filename = data.get("filename", "unknown.json")

        if not tenders or not isinstance(tenders, list):
            return jsonify({"error": "Тендерийн мэдээлэл хоосон байна"}), 400

        conn    = get_db()
        cur     = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        user_id = session["user_id"]
        now     = datetime.utcnow().isoformat()
        inserted = updated = unchanged = 0
        changes = []

        try:
            for t in tenders:
                tender_no = t.get("tender_no", "").strip()
                if not tender_no:
                    continue

                cur.execute(
                    "SELECT id, name, organization, type, method, deadline, electronic, link "
                    "FROM tenders WHERE tender_no = %s", (tender_no,)
                )
                existing = cur.fetchone()

                if existing is None:
                    cur.execute("""
                        INSERT INTO tenders
                            (tender_no, name, organization, type, method, deadline,
                             electronic, link, raw_data, created_by, created_at, updated_at)
                        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                    """, (
                        tender_no,
                        t.get("name"), t.get("organization"), t.get("type"),
                        t.get("method"), t.get("deadline"), t.get("electronic"), t.get("link"),
                        json.dumps(t, ensure_ascii=False), user_id, now, now
                    ))
                    inserted += 1
                    changes.append({"tender_no": tender_no, "action": "new", "name": t.get("name", "")})
                else:
                    diffs = {
                        f: {"old": existing[f] or "", "new": t.get(f, "") or ""}
                        for f in TENDER_FIELDS
                        if str(existing[f] or "") != str(t.get(f, "") or "")
                    }
                    if diffs:
                        cur.execute("""
                            UPDATE tenders
                            SET name=%s, organization=%s, type=%s, method=%s, deadline=%s,
                                electronic=%s, link=%s, raw_data=%s, updated_at=%s
                            WHERE tender_no=%s
                        """, (
                            t.get("name"), t.get("organization"), t.get("type"),
                            t.get("method"), t.get("deadline"), t.get("electronic"), t.get("link"),
                            json.dumps(t, ensure_ascii=False), now, tender_no
                        ))
                        updated += 1
                        changes.append({"tender_no": tender_no, "action": "updated",
                                        "name": t.get("name", ""), "diffs": diffs})
                    else:
                        unchanged += 1

            cur.execute("""
                INSERT INTO upload_history (user_id, filename, total_in_file, inserted, updated, unchanged)
                VALUES (%s,%s,%s,%s,%s,%s)
            """, (user_id, filename, len(tenders), inserted, updated, unchanged))
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            release_db(conn)

        return jsonify({
            "success": True,
            "summary": {"total_in_file": len(tenders), "inserted": inserted,
                        "updated": updated, "unchanged": unchanged},
            "changes": changes[:50]
        })

    @app.route("/api/tenders")
    @login_required
    def get_tenders():
        total  = query("SELECT COUNT(*) as cnt FROM tenders", fetchone=True)["cnt"]
        limit  = min(int(request.args.get("limit", 20000)), 50000)
        offset = int(request.args.get("offset", 0))
        rows   = query("""
            SELECT tender_no, name, organization, type, method, deadline, electronic, link,
                   created_at, updated_at
            FROM tenders ORDER BY deadline ASC LIMIT %s OFFSET %s
        """, (limit, offset), fetchall=True)
        return jsonify({"total": total, "tenders": rows})

    @app.route("/api/tenders/stats")
    @login_required
    def tender_stats():
        total   = query("SELECT COUNT(*) as cnt FROM tenders", fetchone=True)["cnt"]
        orgs    = query("SELECT COUNT(DISTINCT organization) as cnt FROM tenders", fetchone=True)["cnt"]
        types   = query("SELECT type, COUNT(*) as cnt FROM tenders GROUP BY type", fetchall=True)
        methods = query("SELECT method, COUNT(*) as cnt FROM tenders GROUP BY method", fetchall=True)
        return jsonify({"total": total, "organizations": orgs, "types": types, "methods": methods})

    @app.route("/api/tenders/history")
    @login_required
    def upload_history():
        rows = query("""
            SELECT h.*, u.full_name, u.username
            FROM upload_history h JOIN users u ON h.user_id = u.id
            ORDER BY h.uploaded_at DESC LIMIT 50
        """, fetchall=True)
        return jsonify(rows)

    @app.route("/api/tenders/match")
    @login_required
    def match_tenders():
        keywords = query("SELECT keyword, weight FROM profile_keywords ORDER BY weight DESC", fetchall=True)
        if not keywords:
            return jsonify({"error": "Профайл үүсгээгүй. Эхлээд 'Профайл үүсгэх' товч дарна уу."}), 400

        source = request.args.get("source", "tenders")
        limit  = min(int(request.args.get("limit", 50)), 200)
        offset = int(request.args.get("offset", 0))

        if source == "plans":
            rows = query(
                "SELECT ts_code as id, name, organization, ts_type as type, "
                "budget_amount, procurement_method as method FROM procurement_plans",
                fetchall=True
            )
        else:
            rows = query("""
                SELECT tender_no as id, name, organization, type, method, deadline, link
                FROM tenders WHERE deadline::timestamp >= NOW() ORDER BY deadline ASC
            """, fetchall=True)

        results = []
        for r in rows:
            name_lower = (r.get("name") or "").lower()
            score, matched = 0, []
            for kw in keywords:
                w  = kw["keyword"]
                wt = float(kw["weight"]) if isinstance(kw["weight"], Decimal) else kw["weight"]
                if w in name_lower:
                    score += wt
                    matched.append(w)
            if score > 0:
                clean = {k: float(v) if isinstance(v, Decimal) else v for k, v in r.items()}
                clean["score"]            = round(score, 2)
                clean["matched_keywords"] = matched
                clean["source"]           = "PLAN" if source == "plans" else "TENDER"
                results.append(clean)

        results.sort(key=lambda x: x["score"], reverse=True)
        max_score = results[0]["score"] if results else 1
        for r in results:
            r["score_pct"] = min(99, round(r["score"] / max_score * 100))

        total = len(results)
        return jsonify({"total": total, "results": results[offset:offset + limit]})
