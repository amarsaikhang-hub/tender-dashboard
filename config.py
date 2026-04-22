import os
import logging
from decimal import Decimal
from datetime import datetime

import anthropic
import openai

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

DB_CONFIG = {
    "host":     os.environ.get("DB_HOST", "localhost"),
    "port":     os.environ.get("DB_PORT", "5432"),
    "dbname":   os.environ.get("DB_NAME", "tender_db"),
    "user":     os.environ.get("DB_USER", "tender_admin"),
    "password": os.environ.get("DB_PASSWORD", "admin_pass"),
}

OVERVIEW_PATH    = os.path.join(BASE_DIR, "overview.html")
DASHBOARD_PATH   = os.path.join(BASE_DIR, "tender-dashboard.html")
PROCUREMENT_PATH = os.path.join(BASE_DIR, "procurement.html")
JOINED_PATH      = os.path.join(BASE_DIR, "joined.html")
SUGGESTED_PATH   = os.path.join(BASE_DIR, "suggested.html")

_anthropic_key = os.environ.get("ANTHROPIC_API_KEY", "")
_openai_key    = os.environ.get("OPENAI_API_KEY", "")

claude_client = anthropic.Anthropic(api_key=_anthropic_key) if _anthropic_key else None
openai_client = openai.OpenAI(api_key=_openai_key)          if _openai_key    else None

CLAUDE_MODEL = os.environ.get("CLAUDE_MODEL", "claude-sonnet-4-6")


def dec(obj):
    if isinstance(obj, Decimal):
        return float(obj)
    if isinstance(obj, datetime):
        return obj.isoformat()
    return obj
