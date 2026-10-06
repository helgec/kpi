import json
import os
import sys
import traceback
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET

# Importer status-hjelperen
sys.path.append("/home/nrknyheter")
from status_helper import update_status

# Sikrer at tilstandsfilen lagres i samme mappe som selve skriptet
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
STATE_FILE = os.path.join(SCRIPT_DIR, "last_seen_kpi.txt")
RSS_URL = "https://www.ssb.no/rss/kpi"

MONTH_NAMES = {
    1: "januar",
    2: "februar",
    3: "mars",
    4: "april",
    5: "mai",
    6: "juni",
    7: "juli",
    8: "august",
    9: "september",
    10: "oktober",
    11: "november",
    12: "desember",
}


def parse_months(tid_str):
    """Konverterer f.eks. '2026M08' til ('juli', 'august')."""
    try:
        if "M" in tid_str:
            _, month_part = tid_str.split("M")
            m_curr = int(month_part)
            m_prev = 12 if m_curr == 1 else m_curr - 1
            return MONTH_NAMES[m_prev], MONTH_NAMES[m_curr]
    except Exception:
        pass
    return "forrige måned", "denne måned"


def fetch_json(url, data=None):
    """Hjelpefunksjon for å hente JSON fra SSB API."""
    headers = {"Content-Type": "application/json", "User-Agent": "Mozilla/5.0"}
    req = urllib.request.Request(
        url,
        data=json.dumps(data).encode("utf-8") if data else None,
        headers=headers,
    )
    try:
        with urllib.request.urlopen(req) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        err_body = e.read().decode("utf-8")
        print(f"❌ HTTP Error {e.code} for URL {url}:\n{err_body}")
        raise


def find_code(var, search_terms):
    """Søker i valueTexts for en variabel og returnerer koden som matcher søkeordene."""
    for val, text in zip(var.get("values", []), var.get("valueTexts", [])):
        text_lower = text.lower()
        if any(term in text_lower for term in search_terms):
            return val
    return None


def fmt_num(val):
    """Formaterer tall til norsk format med komma (f.eks. 3.0 -> 3,0)."""
    if val is None:
        return "N/A"
    if isinstance(val, (int, float)):
        return f"{val:.1f}".replace(".", ",")
    return str(val).replace(".", ",")


def verb_word(val):
    """Returnerer 'sank' for negative tall, ellers 'steg'."""
    return "sank" if (isinstance(val, (int, float)) and val < 0) else "steg"


def abs_fmt(val):
    """Returnerer absoluttverdi formatert (f.eks. -0.5 -> 0,5)."""
    return fmt_num(abs(val)) if isinstance(val, (int, float)) else fmt_num(val)


def get_kpi_metrics():
    """
    Henter KPI-tall fra SSBs API:
    - Total (00) 12-mnd vekst fra Tabell 14700.
    - Matvarer (01.1) 12-mnd vekst OG månedsendring fra Tabell 14700.
    - KPI-JAE (kjerneinflasjon) 12-mnd vekst fra Tabell 14706 / 14704.
    """
    xx, zz, zz_mnd, yy, latest_tid = None, None, None, None, None

    # 1. Hent KPI Total (00) og Matvarer (01.1) fra Tabell 14700
    try:
        print(
            "🔍 Henter KPI Total og Matvarer (12mnd + mnd-endring) fra Tabell"
            " 14700..."
        )
        meta_14700
