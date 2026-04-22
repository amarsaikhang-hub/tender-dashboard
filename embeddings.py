import logging
from decimal import Decimal

from flask import request, jsonify

from db import get_db, release_db, query
from auth import login_required, editor_required
from config import openai_client


def chunk_text(text, max_len=500):
    words, chunks, current, length = text.split(), [], [], 0
    for w in words:
        current.append(w)
        length += len(w) + 1
        if length >= max_len:
            chunks.append(" ".join(current))
            current, length = [], 0
    if current:
        chunks.append(" ".join(current))
    return chunks if chunks else [text[:max_len]]


def embed_texts(texts):
    if not openai_client:
        return None
    resp = openai_client.embeddings.create(model="text-embedding-3-small", input=texts)
    return [d.embedding for d in resp.data]


def register(app):
    @app.route("/api/embedding/store", methods=["POST"])
    @editor_required
    def store_embedding():
        data      = request.get_json()
        tender_no = data.get("tender_no", "").strip()

        if not tender_no:
            return jsonify({"error": "tender_no шаардлагатай"}), 400

        detail = query(
            "SELECT requirements, ocr_text FROM tender_details WHERE tender_no = %s",
            (tender_no,), fetchone=True
        )
        if not detail:
            return jsonify({"error": "Тендерийн мэдээлэл олдсонгүй"}), 404

        text = (detail.get("requirements") or detail.get("ocr_text") or "").strip()
        if not text:
            return jsonify({"error": "Текст хоосон"}), 400

        chunks     = chunk_text(text)
        embeddings = embed_texts(chunks)
        if embeddings is None:
            return jsonify({"error": "OPENAI_API_KEY тохируулаагүй"}), 500

        conn = get_db()
        cur  = conn.cursor()
        cur.execute("DELETE FROM tender_embeddings WHERE tender_no = %s", (tender_no,))
        for i, (chunk, emb) in enumerate(zip(chunks, embeddings)):
            cur.execute("""
                INSERT INTO tender_embeddings (tender_no, chunk_index, chunk_text, embedding, source)
                VALUES (%s,%s,%s,%s,'ocr')
            """, (tender_no, i, chunk, str(emb)))
        conn.commit()
        release_db(conn)
        return jsonify({"success": True, "chunks": len(chunks), "tender_no": tender_no})

    @app.route("/api/embedding/search", methods=["POST"])
    @login_required
    def search_embedding():
        data       = request.get_json()
        query_text = data.get("query", "").strip()
        limit      = int(data.get("limit", 10))

        if not query_text:
            return jsonify({"error": "Хайлтын текст оруулна уу"}), 400

        q_emb = embed_texts([query_text])
        if q_emb is None:
            return jsonify({"error": "OPENAI_API_KEY тохируулаагүй"}), 500

        rows = query("""
            SELECT tender_no, chunk_text, chunk_index,
                   1 - (embedding <=> %s::vector) as similarity
            FROM tender_embeddings
            ORDER BY embedding <=> %s::vector LIMIT %s
        """, (str(q_emb[0]), str(q_emb[0]), limit), fetchall=True)

        for r in rows:
            for k, v in r.items():
                if isinstance(v, Decimal): r[k] = float(v)

        return jsonify({"results": rows})

    @app.route("/api/embedding/batch", methods=["POST"])
    @editor_required
    def batch_embedding():
        details = query("""
            SELECT tender_no, COALESCE(requirements,'') || ' ' || COALESCE(ocr_text,'') as text
            FROM tender_details
            WHERE tender_no NOT IN (SELECT DISTINCT tender_no FROM tender_embeddings)
            LIMIT 50
        """, fetchall=True)

        if not details:
            return jsonify({"message": "Бүх текст embedding хийгдсэн", "processed": 0})

        processed = errors = 0
        for d in details:
            text = d["text"].strip()
            if not text or len(text) < 10:
                continue
            try:
                chunks     = chunk_text(text)
                embeddings = embed_texts(chunks)
                if not embeddings:
                    continue
                conn = get_db()
                cur  = conn.cursor()
                for i, (chunk, emb) in enumerate(zip(chunks, embeddings)):
                    cur.execute("""
                        INSERT INTO tender_embeddings
                            (tender_no, chunk_index, chunk_text, embedding, source)
                        VALUES (%s,%s,%s,%s,'ocr')
                    """, (d["tender_no"], i, chunk, str(emb)))
                conn.commit()
                release_db(conn)
                processed += 1
            except Exception as e:
                logging.warning("Batch embedding алдаа [%s]: %s", d["tender_no"], e)
                errors += 1

        return jsonify({"processed": processed, "errors": errors,
                        "remaining": len(details) - processed})

    @app.route("/api/embedding/stats")
    @login_required
    def embedding_stats():
        stats = query("""
            SELECT COUNT(DISTINCT tender_no) as tenders, COUNT(*) as chunks
            FROM tender_embeddings
        """, fetchone=True)
        return jsonify(stats)
