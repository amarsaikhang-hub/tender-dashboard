#!/usr/bin/env python3
import os
import sys
import logging
import requests
import psycopg2
import psycopg2.extras
from datetime import datetime
from pathlib import Path

BASE_DIR = Path(__file__).parent

# Load .env
env_file = BASE_DIR / ".env"
if env_file.exists():
    for line in env_file.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(BASE_DIR / "logs" / "teams_notify.log"),
        logging.StreamHandler(),
    ],
)
log = logging.getLogger(__name__)

TEAMS_WEBHOOK_URL = os.environ.get("TEAMS_WEBHOOK_URL", "")

DB_CONFIG = {
    "host":     os.environ.get("DB_HOST", "localhost"),
    "port":     os.environ.get("DB_PORT", "5432"),
    "dbname":   os.environ.get("DB_NAME", "tender_db"),
    "user":     os.environ.get("DB_USER", "tender_admin"),
    "password": os.environ.get("DB_PASSWORD", "admin_pass"),
}


def get_joined_orgs(conn):
    with conn.cursor() as cur:
        cur.execute("""
            SELECT DISTINCT organization
            FROM joined_tenders
            WHERE organization IS NOT NULL AND organization != ''
            ORDER BY organization
        """)
        return {row[0] for row in cur.fetchall()}


def get_new_tenders(conn, joined_orgs):
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute("""
            SELECT tender_no, name, organization, type, method, deadline, link, created_at
            FROM tenders
            WHERE created_at >= NOW() - INTERVAL '24 hours'
              AND organization = ANY(%s)
            ORDER BY created_at DESC
        """, (list(joined_orgs),))
        return cur.fetchall()


MAX_TENDERS_PER_CARD = 15


def _tender_row(t):
    deadline = str(t.get("deadline") or "—").strip()
    org      = t.get("organization") or "—"
    method   = t.get("method")       or "—"
    name     = t.get("name")         or "—"
    no       = t.get("tender_no")    or ""

    return {
        "type": "ColumnSet",
        "spacing": "Medium",
        "columns": [
            {
                "type": "Column",
                "width": "auto",
                "items": [{
                    "type": "TextBlock",
                    "text": no,
                    "color": "Accent",
                    "weight": "Bolder",
                    "size": "Small",
                    "wrap": False,
                }],
            },
            {
                "type": "Column",
                "width": "stretch",
                "items": [
                    {
                        "type": "TextBlock",
                        "text": name,
                        "weight": "Bolder",
                        "size": "Small",
                        "wrap": True,
                        "spacing": "None",
                    },
                    {
                        "type": "TextBlock",
                        "text": f"🏢 {org}",
                        "isSubtle": True,
                        "size": "Small",
                        "wrap": True,
                        "spacing": "None",
                    },
                    {
                        "type": "TextBlock",
                        "text": f"📋 {method}  •  📅 {deadline}",
                        "isSubtle": True,
                        "size": "Small",
                        "wrap": True,
                        "spacing": "None",
                    },
                ],
            },
        ],
    }


def build_adaptive_card(tenders):
    date_str  = datetime.now().strftime("%Y-%m-%d %H:%M")
    total     = len(tenders)
    shown     = tenders[:MAX_TENDERS_PER_CARD]
    remaining = total - len(shown)

    body = [
        # Гарчиг хэсэг
        {
            "type": "ColumnSet",
            "columns": [
                {
                    "type": "Column",
                    "width": "stretch",
                    "items": [
                        {
                            "type": "TextBlock",
                            "text": "🔔 Сүүлийн 24 цагт нийтлэгдсэн тендер",
                            "weight": "Bolder",
                            "size": "Large",
                            "color": "Accent",
                            "wrap": True,
                        },
                        {
                            "type": "TextBlock",
                            "text": f"{date_str}",
                            "isSubtle": True,
                            "size": "Small",
                            "spacing": "None",
                        },
                    ],
                },
                {
                    "type": "Column",
                    "width": "auto",
                    "verticalContentAlignment": "Center",
                    "items": [{
                        "type": "TextBlock",
                        "text": f"{total} тендер",
                        "weight": "Bolder",
                        "size": "ExtraLarge",
                        "color": "Accent",
                        "horizontalAlignment": "Right",
                    }],
                },
            ],
        },
        {"type": "TextBlock", "text": "---", "separator": True, "spacing": "Small"},
    ]

    actions = []
    for t in shown:
        body.append(_tender_row(t))
        link = str(t.get("link") or "").strip()
        if link:
            actions.append({
                "type": "Action.OpenUrl",
                "title": t.get("tender_no") or "Холбоос",
                "url": link,
            })

    if remaining > 0:
        body.append({
            "type": "TextBlock",
            "text": f"_... болон {remaining} тендер байна. Дэлгэрэнгүйг дашбоардаас харна уу._",
            "isSubtle": True,
            "size": "Small",
            "spacing": "Medium",
            "wrap": True,
        })

    return {
        "type": "AdaptiveCard",
        "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
        "version": "1.2",
        "body": body,
        "actions": actions,
    }


def build_empty_card(org_count):
    date_str = datetime.now().strftime("%Y-%m-%d %H:%M")
    return {
        "type": "AdaptiveCard",
        "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
        "version": "1.2",
        "body": [
            {
                "type": "ColumnSet",
                "columns": [
                    {
                        "type": "Column",
                        "width": "stretch",
                        "items": [
                            {
                                "type": "TextBlock",
                                "text": "🔔 Сүүлийн 24 цагт нийтлэгдсэн тендер",
                                "weight": "Bolder",
                                "size": "Large",
                                "color": "Accent",
                                "wrap": True,
                            },
                            {
                                "type": "TextBlock",
                                "text": date_str,
                                "isSubtle": True,
                                "size": "Small",
                                "spacing": "None",
                            },
                        ],
                    },
                    {
                        "type": "Column",
                        "width": "auto",
                        "verticalContentAlignment": "Center",
                        "items": [{
                            "type": "TextBlock",
                            "text": "0 тендер",
                            "weight": "Bolder",
                            "size": "ExtraLarge",
                            "color": "Good",
                            "horizontalAlignment": "Right",
                        }],
                    },
                ],
            },
            {"type": "TextBlock", "text": "---", "separator": True, "spacing": "Small"},
            {
                "type": "TextBlock",
                "text": f"✅ Таны **{org_count} байгууллага**-аас сүүлийн 24 цагт шинэ тендер зарлаагүй байна.",
                "wrap": True,
                "spacing": "Medium",
            },
        ],
    }


def send_to_teams(tenders, org_count):
    if not tenders:
        log.info("Сүүлийн 24 цагт шинэ тендер олдсонгүй — мэдэгдэл илгээж байна.")
        card = build_empty_card(org_count)
    else:
        card = build_adaptive_card(tenders)

    resp = requests.post(TEAMS_WEBHOOK_URL, json=card, timeout=30)
    resp.raise_for_status()
    log.info(f"Teams-д илгээлээ. Статус: {resp.status_code}")


def main():
    if not TEAMS_WEBHOOK_URL:
        log.error("TEAMS_WEBHOOK_URL .env файлд тохируулаагүй байна")
        sys.exit(1)

    try:
        conn = psycopg2.connect(**DB_CONFIG)
        joined_orgs = get_joined_orgs(conn)
        log.info(f"Оролцсон байгууллага: {len(joined_orgs)} ш")

        if not joined_orgs:
            log.warning("joined_tenders хүснэгтэд байгууллага олдсонгүй")
            conn.close()
            return

        tenders = get_new_tenders(conn, joined_orgs)
        log.info(f"Сүүлийн 24 цагт шинэ тендер: {len(tenders)} ш")

        send_to_teams(tenders, len(joined_orgs))
        conn.close()
    except Exception:
        log.exception("Алдаа гарлаа")
        sys.exit(1)


if __name__ == "__main__":
    main()
