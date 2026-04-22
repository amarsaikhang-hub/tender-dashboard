import io
import logging
import os

import pytesseract
from PIL import Image
from pdf2image import convert_from_bytes
from flask import request, jsonify, session

from db import get_db, release_db
from auth import login_required, editor_required
from config import claude_client, CLAUDE_MODEL
from embeddings import chunk_text, embed_texts


def register(app):
    @app.route("/api/ocr/upload", methods=["POST"])
    @login_required
    def ocr_upload():
        if "file" not in request.files:
            return jsonify({"error": "Файл сонгоно уу"}), 400

        file      = request.files["file"]
        tender_no = request.form.get("tender_no", "")
        fname     = file.filename.lower()
        raw_text  = ""

        try:
            file_bytes = file.read()
            if fname.endswith(".pdf"):
                for img in convert_from_bytes(file_bytes, dpi=300):
                    raw_text += pytesseract.image_to_string(img, lang="mon+eng") + "\n"
            elif fname.endswith((".png", ".jpg", ".jpeg", ".bmp", ".tiff")):
                raw_text = pytesseract.image_to_string(Image.open(io.BytesIO(file_bytes)), lang="mon+eng")
            else:
                return jsonify({"error": "PDF эсвэл зураг файл дэмжигдэнэ"}), 400
        except Exception as e:
            return jsonify({"error": f"OCR алдаа: {str(e)}"}), 500

        if not raw_text.strip():
            return jsonify({"error": "Текст уншиж чадсангүй. Зургийн чанарыг шалгана уу."}), 400

        if not claude_client:
            return jsonify({
                "ocr_text": raw_text, "corrected": raw_text, "requirements": [],
                "warning": "ANTHROPIC_API_KEY тохируулаагүй — OCR текст засварлагдаагүй"
            })

        try:
            msg = claude_client.messages.create(
                model=CLAUDE_MODEL,
                max_tokens=3000,
                messages=[{"role": "user", "content": f"""Дараах текст нь тендерийн баримт бичгээс OCR-ээр уншсан. Монгол кирилл алдааг засаж, шаардлагуудыг задал.

OCR ТЕКСТ:
{raw_text[:4000]}

ДААЛГАВАР:
1. OCR алдааг засварла (үсэг солигдсон, тэмдэгт алдаа)
2. Шаардлагуудыг жагсаалтаар гарга
3. JSON хариул:

{{"corrected_text": "засварласан бүрэн текст", "requirements": [{{"id": "3.1", "text": "шаардлагын текст", "category": "ерөнхий/техникийн/санхүүгийн/туршлагын"}}], "summary": "баримт бичгийн товч тайлбар"}}

Зөвхөн JSON хариул."""}]
            )

            raw    = msg.content[0].text.strip()
            result = None
            try:
                import json
                result = json.loads(raw)
            except (json.JSONDecodeError, ValueError):
                import json
                start, end = raw.find("{"), raw.rfind("}")
                if start >= 0 and end > start:
                    try:    result = json.loads(raw[start:end + 1])
                    except: pass

            if not result:
                result = {"corrected_text": raw_text, "requirements": [], "summary": ""}

            result["ocr_text"]  = raw_text
            result["tender_no"] = tender_no

            if tender_no:
                try:
                    emb_text = result.get("corrected_text") or raw_text
                    if emb_text and len(emb_text.strip()) > 10:
                        chunks     = chunk_text(emb_text)
                        embeddings = embed_texts(chunks)
                        if embeddings:
                            conn = get_db()
                            cur  = conn.cursor()
                            cur.execute("DELETE FROM tender_embeddings WHERE tender_no = %s", (tender_no,))
                            for i, (ch, em) in enumerate(zip(chunks, embeddings)):
                                cur.execute(
                                    "INSERT INTO tender_embeddings "
                                    "(tender_no, chunk_index, chunk_text, embedding) VALUES (%s,%s,%s,%s)",
                                    (tender_no, i, ch, str(em))
                                )
                            conn.commit()
                            release_db(conn)
                            result["embedding_chunks"] = len(chunks)
                except Exception as e:
                    logging.warning("OCR embedding алдаа: %s", e)

            return jsonify(result)

        except Exception as e:
            return jsonify({
                "ocr_text": raw_text, "corrected_text": raw_text,
                "requirements": [], "error": f"Claude алдаа: {str(e)}"
            })

    @app.route("/api/ocr/batch", methods=["POST"])
    @editor_required
    def ocr_batch():
        data        = request.get_json()
        folder_path = data.get("folder", "").strip()

        if not folder_path or not os.path.isdir(folder_path):
            return jsonify({"error": f"Фолдер олдсонгүй: {folder_path}"}), 400

        files = [f for f in os.listdir(folder_path)
                 if f.lower().endswith((".pdf", ".png", ".jpg", ".jpeg"))]
        if not files:
            return jsonify({"error": "Фолдерт PDF/зураг файл олдсонгүй"}), 400

        user_id   = session["user_id"]
        processed = errors = 0
        results   = []

        for fname in sorted(files):
            name_part = os.path.splitext(fname)[0]
            if "_шаардлага" in name_part:
                tender_no = name_part.replace("_шаардлага", "").strip()
                doc_type  = "requirements"
            elif "_унасан" in name_part:
                tender_no = name_part.replace("_унасан", "").strip()
                doc_type  = "fail_reason"
            else:
                results.append({"file": fname, "status": "skipped",
                                 "reason": "Нэр таниулгүй (_шаардлага эсвэл _унасан байх ёстой)"})
                continue

            tender_no = tender_no.replace("_", "/", 1)
            filepath  = os.path.join(folder_path, fname)

            try:
                raw_text = ""
                if fname.lower().endswith(".pdf"):
                    with open(filepath, "rb") as f:
                        for img in convert_from_bytes(f.read(), dpi=300):
                            raw_text += pytesseract.image_to_string(img, lang="mon+eng") + "\n"
                else:
                    raw_text = pytesseract.image_to_string(Image.open(filepath), lang="mon+eng")

                if not raw_text.strip():
                    results.append({"file": fname, "tender_no": tender_no,
                                    "type": doc_type, "status": "error", "reason": "Текст уншигдсангүй"})
                    errors += 1
                    continue

                final_text = raw_text
                if claude_client:
                    try:
                        msg = claude_client.messages.create(
                            model=CLAUDE_MODEL, max_tokens=2000,
                            messages=[{"role": "user", "content":
                                f"Дараах OCR текстийн Монгол кирилл алдааг засварла. "
                                f"Зөвхөн засварласан текстийг хариул:\n\n{raw_text[:3000]}"}]
                        )
                        final_text = msg.content[0].text.strip()
                    except Exception as e:
                        logging.warning("Batch OCR Claude засвар алдаа: %s", e)

                conn = get_db()
                cur  = conn.cursor()
                cur.execute("SELECT id FROM tender_details WHERE tender_no = %s", (tender_no,))
                if cur.fetchone():
                    if doc_type == "requirements":
                        cur.execute("""
                            UPDATE tender_details
                            SET requirements=%s, ocr_text=%s, updated_by=%s, updated_at=NOW()
                            WHERE tender_no=%s
                        """, (final_text, raw_text, user_id, tender_no))
                    else:
                        cur.execute("""
                            UPDATE tender_details
                            SET fail_reason=%s, updated_by=%s, updated_at=NOW()
                            WHERE tender_no=%s
                        """, (final_text, user_id, tender_no))
                else:
                    if doc_type == "requirements":
                        cur.execute(
                            "INSERT INTO tender_details (tender_no, requirements, ocr_text, updated_by) "
                            "VALUES (%s,%s,%s,%s)",
                            (tender_no, final_text, raw_text, user_id)
                        )
                    else:
                        cur.execute(
                            "INSERT INTO tender_details (tender_no, fail_reason, updated_by) "
                            "VALUES (%s,%s,%s)",
                            (tender_no, final_text, user_id)
                        )

                conn.commit()
                release_db(conn)
                processed += 1
                results.append({"file": fname, "tender_no": tender_no,
                                 "type": doc_type, "status": "ok"})

            except Exception as e:
                errors += 1
                results.append({"file": fname, "tender_no": tender_no,
                                 "type": doc_type, "status": "error", "reason": str(e)})

        return jsonify({
            "success": True,
            "total_files": len(files),
            "processed":   processed,
            "errors":      errors,
            "skipped":     len(files) - processed - errors,
            "results":     results
        })
