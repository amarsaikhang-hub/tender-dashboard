import json
import logging
from decimal import Decimal

from flask import request, jsonify, session

from db import get_db, release_db, query
from auth import login_required, editor_required
from config import claude_client, CLAUDE_MODEL, dec
from embeddings import embed_texts


def register(app):
    # ── AI: тулгах ──────────────────────────────────────────────
    @app.route("/api/ai/match", methods=["POST"])
    @login_required
    def ai_match():
        if not claude_client:
            return jsonify({"error": "ANTHROPIC_API_KEY тохируулаагүй байна"}), 500

        data       = request.get_json()
        plan_name  = data.get("plan_name", "")
        candidates = data.get("candidates", [])

        if not plan_name or not candidates:
            return jsonify({"error": "plan_name болон candidates шаардлагатай"}), 400

        cand_text = "\n".join([f"- [{c['tender_no']}] {c['name']}" for c in candidates[:30]])
        msg = claude_client.messages.create(
            model=CLAUDE_MODEL, max_tokens=500,
            messages=[{"role": "user", "content": f"""Дараах худалдан авалтын төлөвлөгөөний нэрийг тендерийн жагсаалтаас утга агуулгаар нь тааруулж өг.

Төлөвлөгөөний нэр: "{plan_name}"

Тендерийн жагсаалт:
{cand_text}

Хэрэв тааралт олдвол JSON-оор хариул: {{"match": true, "tender_no": "...", "confidence": 0.0-1.0, "reason": "..."}}
Хэрэв олдохгүй бол: {{"match": false, "reason": "..."}}
Зөвхөн JSON хариул, өөр юу ч бичих хэрэггүй."""}]
        )
        try:
            result = json.loads(msg.content[0].text)
        except (json.JSONDecodeError, ValueError):
            result = {"match": False, "reason": msg.content[0].text}
        return jsonify(result)

    # ── AI: шинжилгээ ────────────────────────────────────────────
    @app.route("/api/ai/analyze", methods=["POST"])
    @login_required
    def ai_analyze():
        if not claude_client:
            return jsonify({"error": "ANTHROPIC_API_KEY тохируулаагүй байна"}), 500

        data     = request.get_json()
        question = data.get("question", "").strip()
        if not question:
            return jsonify({"error": "Асуулт оруулна уу"}), 400

        tender_stats  = query("""
            SELECT COUNT(*) as total,
                   COUNT(CASE WHEN deadline::timestamp >= NOW() THEN 1 END) as active,
                   COUNT(CASE WHEN deadline::timestamp <  NOW() THEN 1 END) as expired,
                   COUNT(DISTINCT organization) as orgs
            FROM tenders
        """, fetchone=True)
        proc_stats    = query("""
            SELECT COUNT(*) as total, COALESCE(SUM(budget_amount),0) as total_budget,
                   COUNT(DISTINCT organization) as orgs, COUNT(DISTINCT ts_type) as types
            FROM procurement_plans
        """, fetchone=True)
        recent        = query("""
            SELECT tender_no, name, organization, type, method, deadline
            FROM tenders ORDER BY deadline DESC LIMIT 20
        """, fetchall=True)
        type_breakdown = query("SELECT type, COUNT(*) as cnt FROM tenders GROUP BY type", fetchall=True)
        proc_types     = query("""
            SELECT ts_type, COUNT(*) as cnt, COALESCE(SUM(budget_amount),0) as budget
            FROM procurement_plans GROUP BY ts_type
        """, fetchall=True)

        context = f"""Тендерийн мэдээлэл:
- Нийт тендер: {tender_stats['total']}, Идэвхтэй: {tender_stats['active']}, Хугацаа дууссан: {tender_stats['expired']}, Байгууллага: {tender_stats['orgs']}
- Төрлөөр: {json.dumps(type_breakdown, default=dec, ensure_ascii=False)}

Худалдан авалтын төлөвлөгөө:
- Нийт: {proc_stats['total']}, Нийт төсөв: {dec(proc_stats['total_budget'])} мян.₮, Байгууллага: {proc_stats['orgs']}
- Төрлөөр: {json.dumps(proc_types, default=dec, ensure_ascii=False)}

Сүүлийн 20 тендер:
{json.dumps(recent, default=dec, ensure_ascii=False)}"""

        msg = claude_client.messages.create(
            model=CLAUDE_MODEL, max_tokens=1500,
            system="Чи тендер болон худалдан авалтын мэдээлэлд дүн шинжилгээ хийх AI туслах юм. Монгол хэлээр хариул. Товч, тодорхой, ашигтай мэдээлэл өг.",
            messages=[{"role": "user", "content": f"Дараах өгөгдөл дээр суурилж асуултад хариул:\n\n{context}\n\nАсуулт: {question}"}]
        )
        return jsonify({"answer": msg.content[0].text, "model": msg.model, "tokens": msg.usage.output_tokens})

    # ── AI: тендер хайх ─────────────────────────────────────────
    @app.route("/api/ai/tender-search", methods=["POST"])
    @login_required
    def ai_tender_search():
        if not claude_client:
            return jsonify({"error": "ANTHROPIC_API_KEY тохируулаагүй"}), 500

        data     = request.get_json()
        question = data.get("question", "").strip()
        if not question:
            return jsonify({"error": "Асуулт оруулна уу"}), 400

        tenders = query("""
            SELECT tender_no, name, organization, type, method, deadline, link
            FROM tenders WHERE deadline::timestamp >= NOW()
            ORDER BY deadline ASC LIMIT 300
        """, fetchall=True)
        tender_stats = query("""
            SELECT COUNT(*) as total,
                   COUNT(CASE WHEN deadline::timestamp >= NOW() THEN 1 END) as active,
                   COUNT(DISTINCT organization) as orgs, COUNT(DISTINCT type) as types
            FROM tenders
        """, fetchone=True)
        tender_list = "\n".join([
            f"[{t['tender_no']}] {t['name']} | {t['organization']} | {t['type']} | {t['method']} | дуусах: {t['deadline']}"
            for t in tenders[:200]
        ])

        msg = claude_client.messages.create(
            model=CLAUDE_MODEL, max_tokens=1500,
            system="""Чи тендерийн мэдээллийн AI туслах юм. Монгол хэлээр хариул.
Хэрэглэгч тендер хайж, санал авч, мэдээлэл асууж болно.

Хэрэв хэрэглэгч тодорхой сэдвээр тендер хайвал тохирох тендерүүдийг JSON массиваар буцаа.
Хариултын төгсгөлд дараах форматаар холбогдох тендерүүдийг оруул:
###TENDERS###
[{"tender_no":"...","name":"...","organization":"...","type":"..."}]
###END###

Хэрэв ерөнхий асуулт бол зөвхөн текст хариул, TENDERS блок оруулах шаардлагагүй.""",
            messages=[{"role": "user", "content":
                f"Тендерийн статистик: нийт {tender_stats['total']}, идэвхтэй {tender_stats['active']}, {tender_stats['orgs']} байгууллага\n\n"
                f"Идэвхтэй тендерүүд:\n{tender_list}\n\nХэрэглэгчийн асуулт: {question}"}]
        )

        answer_text   = msg.content[0].text
        found_tenders = []
        if "###TENDERS###" in answer_text:
            parts       = answer_text.split("###TENDERS###")
            answer_text = parts[0].strip()
            try:
                tender_json   = parts[1].split("###END###")[0].strip()
                found_tenders = json.loads(tender_json)
            except (json.JSONDecodeError, ValueError, IndexError):
                pass

        return jsonify({"answer": answer_text, "tenders": found_tenders[:20], "model": msg.model})

    # ── AI: cross-analysis ───────────────────────────────────────
    @app.route("/api/ai/cross-analysis", methods=["POST"])
    @login_required
    def cross_analysis():
        if not claude_client:
            return jsonify({"error": "ANTHROPIC_API_KEY тохируулаагүй"}), 500

        data      = request.get_json()
        tender_no = data.get("tender_no", "").strip()
        if not tender_no:
            return jsonify({"error": "tender_no шаардлагатай"}), 400

        detail = query("SELECT requirements, fail_reason FROM tender_details WHERE tender_no = %s",
                       (tender_no,), fetchone=True)
        if not detail or not detail.get("requirements"):
            return jsonify({"error": "Шаардлага оруулаагүй байна. Эхлээд PDF оруулна уу."}), 400

        requirements = detail["requirements"]
        fail_reason  = detail.get("fail_reason") or ""
        chunks       = query("""
            SELECT chunk_text FROM tender_embeddings WHERE tender_no = %s ORDER BY chunk_index
        """, (tender_no,), fetchall=True)
        chunk_texts  = "\n".join([c["chunk_text"] for c in chunks]) if chunks else requirements
        tender       = query("SELECT * FROM joined_tenders WHERE tender_no = %s", (tender_no,), fetchone=True)
        tender_info  = (f"Тендер: {tender.get('name','')} | Байгууллага: {tender.get('organization','')} | "
                        f"Статус: {tender.get('status','')}") if tender else ""

        msg = claude_client.messages.create(
            model=CLAUDE_MODEL, max_tokens=3000,
            messages=[{"role": "user", "content": f"""Тендерийн шаардлага болон унасан шалтгааг харьцуулж дүн шинжилгээ хий.

{tender_info}

=== ТЕНДЕРИЙН ШААРДЛАГУУД ===
{requirements[:3000]}

=== БАРИМТ БИЧГИЙН ДЭЛГЭРЭНГҮЙ (OCR) ===
{chunk_texts[:3000]}

=== УНАСАН ШАЛТГААН (хэрэв байгаа бол) ===
{fail_reason[:1000]}

ДААЛГАВАР:
Шаардлага бүрийг шинжилж, компани хангаж чадсан/чадаагүй эсэхийг дүгнэ.

JSON хариул:
{{"summary":"Ерөнхий дүгнэлт","total_requirements":тоо,"met":хангасан_тоо,"not_met":хангаагүй_тоо,"unclear":тодорхойгүй_тоо,"analysis":[{{"id":"заалтын дугаар","requirement":"шаардлага","status":"met/not_met/unclear","reason":"тайлбар","recommendation":"зөвлөмж"}}],"key_lessons":["Сургамж 1"]}}
Зөвхөн JSON хариул."""}]
        )

        raw    = msg.content[0].text.strip()
        result = None
        try:
            result = json.loads(raw)
        except (json.JSONDecodeError, ValueError):
            start, end = raw.find("{"), raw.rfind("}")
            if start >= 0 and end > start:
                try:    result = json.loads(raw[start:end + 1])
                except: pass
        if not result:
            result = {"summary": raw[:500], "analysis": []}

        try:
            analysis_text = result.get("summary", "") + " " + " ".join([
                f"{a.get('id','')} {a.get('requirement','')} {a.get('status','')} {a.get('reason','')}"
                for a in result.get("analysis", [])
            ])
            if analysis_text.strip():
                embs = embed_texts([analysis_text[:2000]])
                if embs:
                    conn = get_db()
                    cur  = conn.cursor()
                    cur.execute("DELETE FROM tender_embeddings WHERE tender_no=%s AND source='analysis'", (tender_no,))
                    cur.execute("""
                        INSERT INTO tender_embeddings (tender_no, chunk_index, chunk_text, embedding, source)
                        VALUES (%s,0,%s,%s,'analysis')
                    """, (tender_no, analysis_text[:2000], str(embs[0])))
                    conn.commit()
                    release_db(conn)
        except Exception as e:
            logging.warning("Cross-analysis embedding алдаа: %s", e)

        result["tender_no"] = tender_no
        return jsonify(result)

    # ── AI: bulk match ───────────────────────────────────────────
    @app.route("/api/ai/bulk-match", methods=["POST"])
    @editor_required
    def ai_bulk_match():
        if not claude_client:
            return jsonify({"error": "ANTHROPIC_API_KEY тохируулаагүй байна"}), 500

        unmatched = query("""
            SELECT p.id, p.ts_code, p.name
            FROM procurement_plans p
            LEFT JOIN LATERAL (
                SELECT t.tender_no FROM tenders t
                WHERE p.ts_code = SPLIT_PART(t.tender_no,'/',1)||'/'||SPLIT_PART(t.tender_no,'/',2)
                   OR LOWER(TRIM(p.name)) = LOWER(TRIM(t.name))
                LIMIT 1
            ) t ON true
            WHERE t.tender_no IS NULL LIMIT 50
        """, fetchall=True)

        if not unmatched:
            return jsonify({"message": "Бүх төлөвлөгөө тулгагдсан байна", "matched": 0})

        tenders = query("""
            SELECT tender_no, name FROM tenders
            WHERE deadline::timestamp >= NOW() ORDER BY deadline ASC LIMIT 500
        """, fetchall=True)
        tender_list = "\n".join([f"[{t['tender_no']}] {t['name']}" for t in tenders[:200]])

        results = []
        for plan in unmatched[:20]:
            try:
                msg = claude_client.messages.create(
                    model=CLAUDE_MODEL, max_tokens=300,
                    messages=[{"role": "user", "content":
                        f"Дараах худалдан авалтыг тендерийн жагсаалтаас утга, агуулгаар тааруул.\n\n"
                        f"Худалдан авалт: \"{plan['name']}\" (код: {plan['ts_code']})\n\n"
                        f"Тендерүүд:\n{tender_list}\n\n"
                        f"JSON хариул: {{\"match\": true/false, \"tender_no\": \"...\", \"confidence\": 0.0-1.0}} Зөвхөн JSON."}]
                )
                r = json.loads(msg.content[0].text)
                r["plan_id"]   = plan["id"]
                r["plan_name"] = plan["name"]
                results.append(r)
            except Exception as e:
                logging.warning("Bulk match алдаа [%s]: %s", plan.get("name", "?"), e)
                continue

        return jsonify({
            "total_unmatched": len(unmatched),
            "processed":       len(results),
            "matches":         [r for r in results if r.get("match")],
            "no_match":        [r for r in results if not r.get("match")]
        })

    # ── Профайл ──────────────────────────────────────────────────
    @app.route("/api/profile/keywords")
    @login_required
    def get_profile_keywords():
        keywords     = query("SELECT keyword, weight, category FROM profile_keywords ORDER BY weight DESC", fetchall=True)
        joined_count = query("SELECT COUNT(*) as cnt FROM joined_tenders", fetchone=True)["cnt"]
        won_count    = query("SELECT COUNT(*) as cnt FROM won_tenders",    fetchone=True)["cnt"]
        active_count = query("SELECT COUNT(*) as cnt FROM tenders WHERE deadline::timestamp >= NOW()", fetchone=True)["cnt"]

        for k in keywords:
            if isinstance(k.get("weight"), Decimal):
                k["weight"] = float(k["weight"])

        return jsonify({"keywords": keywords, "joined_count": joined_count,
                        "won_count": won_count, "active_count": active_count})

    @app.route("/api/profile/generate", methods=["POST"])
    @editor_required
    def generate_profile():
        if not claude_client:
            return jsonify({"error": "ANTHROPIC_API_KEY тохируулаагүй"}), 500

        joined = query("SELECT name, organization, tender_type FROM joined_tenders ORDER BY id DESC LIMIT 300", fetchall=True)
        won    = query("SELECT name, tender_type FROM won_tenders LIMIT 100", fetchall=True)
        if not joined:
            return jsonify({"error": "Оролцсон тендер оруулаагүй"}), 400

        names     = [j["name"] for j in joined if j["name"]]
        won_names = [w["name"] for w in won    if w["name"]]

        msg = claude_client.messages.create(
            model=CLAUDE_MODEL, max_tokens=2000,
            messages=[{"role": "user", "content":
                f"Дараах тендерийн нэрсийг шинжилж компанийн чиглэлийн ТҮЛХҮҮР ҮГС гарга.\n\n"
                f"ОРОЛЦСОН ТЕНДЕРҮҮД:\n{'; '.join(names[:100])}\n\n"
                f"ШАЛГАРСАН:\n{'; '.join(won_names[:30])}\n\n"
                f"ДААЛГАВАР: Эдгээр тендерээс мэргэжлийн түлхүүр үгс гарга. "
                f"Ерөнхий үгс (тендер, ажил, бараа, худалдан) ОРУУЛАХГҮЙ. Зөвхөн мэргэжлийн тодорхой үгс.\n\n"
                f"JSON хариул:\n"
                f"[{{\"keyword\":\"трансформатор\",\"weight\":0.9,\"category\":\"тоног төхөөрөмж\"}}]\n"
                f"Зөвхөн JSON массив, 30-50 түлхүүр үг."}]
        )

        raw     = msg.content[0].text.strip()
        kw_list = None
        try:
            kw_list = json.loads(raw)
        except (json.JSONDecodeError, ValueError):
            start, end = raw.find("["), raw.rfind("]")
            if start >= 0 and end > start:
                try:    kw_list = json.loads(raw[start:end + 1])
                except: pass

        if not kw_list or not isinstance(kw_list, list):
            return jsonify({"error": "AI хариулт задлах чадсангүй"}), 500

        conn = get_db()
        cur  = conn.cursor()
        cur.execute("DELETE FROM profile_keywords")
        inserted = 0
        for kw in kw_list:
            word = (kw.get("keyword") or "").strip().lower()
            if not word or len(word) < 2:
                continue
            weight = float(kw.get("weight", 1.0))
            cat    = (kw.get("category") or "").strip()
            cur.execute(
                "INSERT INTO profile_keywords (keyword, weight, category) VALUES (%s,%s,%s) "
                "ON CONFLICT (keyword) DO UPDATE SET weight=%s, category=%s",
                (word, weight, cat, weight, cat)
            )
            inserted += 1
        conn.commit()
        release_db(conn)
        return jsonify({"success": True, "count": inserted, "keywords": kw_list})

    @app.route("/api/profile/keywords", methods=["PUT"])
    @editor_required
    def update_profile_keyword():
        data    = request.get_json()
        action  = data.get("action", "add")
        keyword = (data.get("keyword") or "").strip().lower()

        if not keyword:
            return jsonify({"error": "Түлхүүр үг оруулна уу"}), 400

        if action == "delete":
            query("DELETE FROM profile_keywords WHERE keyword = %s", (keyword,), commit=True)
        else:
            weight = float(data.get("weight", 1.0))
            cat    = (data.get("category") or "").strip()
            query(
                "INSERT INTO profile_keywords (keyword, weight, category) VALUES (%s,%s,%s) "
                "ON CONFLICT (keyword) DO UPDATE SET weight=%s, category=%s",
                (keyword, weight, cat, weight, cat), commit=True
            )
        return jsonify({"success": True})
