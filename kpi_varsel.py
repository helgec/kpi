import os
import sys
import json
import traceback
import urllib.request
import urllib.error
import xml.etree.ElementTree as ET

# Sikrer at tilstandsfilen lagres i samme mappe som selve skriptet
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
STATE_FILE = os.path.join(SCRIPT_DIR, "last_seen_kpi.txt")
RSS_URL = "https://www.ssb.no/rss/kpi"

MONTH_NAMES = {
    1: "januar", 2: "februar", 3: "mars", 4: "april",
    5: "mai", 6: "juni", 7: "juli", 8: "august",
    9: "september", 10: "oktober", 11: "november", 12: "desember"
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
    headers = {'Content-Type': 'application/json', 'User-Agent': 'Mozilla/5.0'}
    req = urllib.request.Request(
        url, 
        data=json.dumps(data).encode('utf-8') if data else None, 
        headers=headers
    )
    try:
        with urllib.request.urlopen(req) as resp:
            return json.loads(resp.read().decode('utf-8'))
    except urllib.error.HTTPError as e:
        err_body = e.read().decode('utf-8')
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
        return f"{val:.1f}".replace('.', ',')
    return str(val).replace('.', ',')

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
        print("🔍 Henter KPI Total og Matvarer (12mnd + mnd-endring) fra Tabell 14700...")
        meta_14700 = fetch_json("https://data.ssb.no/api/v0/no/table/14700")
        vars_14700 = meta_14700["variables"]
        
        cnt_var = next(v for v in vars_14700 if v["code"] == "ContentsCode")
        tid_var = next(v for v in vars_14700 if v["code"] == "Tid")
        grp_var = next(v for v in vars_14700 if v["code"] not in ["ContentsCode", "Tid"])

        total_code = "00" if "00" in grp_var["values"] else grp_var["values"][0]
        mat_code = "01.1" if "01.1" in grp_var["values"] else (find_code(grp_var, ["01.1", "matvarer"]) or "01.1")
        
        m12_code = find_code(cnt_var, ["12-måned", "endringaar", "tolv", "12 mnd"]) or cnt_var["values"][-1]
        m1_code = find_code(cnt_var, ["månedsendring", "mnd-endring", "måned", "1-måned"]) or cnt_var["values"][1]
        
        latest_tid = tid_var["values"][-1]

        q_14700 = {
            "query": [
                {"code": grp_var["code"], "selection": {"filter": "item", "values": [total_code, mat_code]}},
                {"code": cnt_var["code"], "selection": {"filter": "item", "values": [m12_code, m1_code]}},
                {"code": tid_var["code"], "selection": {"filter": "item", "values": [latest_tid]}}
            ],
            "response": {"format": "json-stat2"}
        }

        res_14700 = fetch_json("https://data.ssb.no/api/v0/no/table/14700", q_14700)
        cat_idx_grp = res_14700["dimension"][grp_var["code"]]["category"]["index"]
        cat_idx_cnt = res_14700["dimension"][cnt_var["code"]]["category"]["index"]
        vals_14700 = res_14700.get("value", [])

        num_cnt = len(cat_idx_cnt)
        
        xx = vals_14700[cat_idx_grp[total_code] * num_cnt + cat_idx_cnt[m12_code]]
        zz = vals_14700[cat_idx_grp[mat_code] * num_cnt + cat_idx_cnt[m12_code]]
        zz_mnd = vals_14700[cat_idx_grp[mat_code] * num_cnt + cat_idx_cnt[m1_code]]

        print(f"✅ Hentet fra 14700 ({latest_tid}): Total 12m={xx}%, Matvarer 12m={zz}%, Matvarer mnd={zz_mnd}%")
    except Exception as e:
        print(f"⚠️ Feil ved henting fra 14700: {e}")

    # 2. Hent KPI-JAE fra Tabell 14706 (fallback til 14704)
    for table_id in ["14706", "14704"]:
        try:
            print(f"🔍 Henter KPI-JAE fra Tabell {table_id}...")
            meta_jae = fetch_json(f"https://data.ssb.no/api/v0/no/table/{table_id}")
            vars_jae = meta_jae["variables"]

            cnt_var = next(v for v in vars_jae if v["code"] == "ContentsCode")
            tid_var = next(v for v in vars_jae if v["code"] == "Tid")
            grp_var = next(v for v in vars_jae if v["code"] not in ["ContentsCode", "Tid"])

            jae_code = find_code(grp_var, ["(kpi-jae)", "kpi-jae"])
            if not jae_code:
                continue

            jae_m12 = find_code(cnt_var, ["12-måned", "endringaar", "tolv", "12 mnd"]) or cnt_var["values"][-1]
            latest_tid_jae = tid_var["values"][-1]

            q_jae = {
                "query": [
                    {"code": grp_var["code"], "selection": {"filter": "item", "values": [jae_code]}},
                    {"code": cnt_var["code"], "selection": {"filter": "item", "values": [jae_m12]}},
                    {"code": tid_var["code"], "selection": {"filter": "item", "values": [latest_tid_jae]}}
                ],
                "response": {"format": "json-stat2"}
            }

            res_jae = fetch_json(f"https://data.ssb.no/api/v0/no/table/{table_id}", q_jae)
            cat_idx_jae = res_jae["dimension"][grp_var["code"]]["category"]["index"]
            vals_jae = res_jae.get("value", [])

            yy = vals_jae[cat_idx_jae[jae_code]]
            if not latest_tid:
                latest_tid = latest_tid_jae
            print(f"✅ Hentet KPI-JAE fra {table_id}: {yy}%")
            break
        except Exception as e:
            print(f"⚠️ Feil ved henting av KPI-JAE fra {table_id}: {e}")

    return xx, zz, zz_mnd, yy, latest_tid

def main():
    slack_url = os.environ.get("SLACK_WEBHOOK_URL", "").strip()
    if not slack_url:
        print("❌ CRITICAL ERROR: Finner ikke SLACK_WEBHOOK_URL som miljøvariabel!")
        sys.exit(1)

    # 1. Hent tallene fra SSBs API
    try:
        xx, zz, zz_mnd, yy, latest_tid = get_kpi_metrics()
        
        if not latest_tid or xx is None or zz is None or zz_mnd is None or yy is None:
            raise ValueError("Klarte ikke hente alle nøkkeltallene fra SSB.")
            
    except Exception as e:
        print(f"❌ Feil ved henting av KPI-tall fra API:")
        traceback.print_exc()
        sys.exit(1)

    # 2. Sjekk om denne perioden allerede er varslet
    last_seen = ""
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE, "r") as f:
            last_seen = f.read().strip()

    if latest_tid == last_seen:
        print(f"ℹ️ Ingen nye KPI-tall siden sist (sist varslet: '{last_seen}'). Avslutter uten å sende til Slack.")
        sys.exit(0)

    print(f"🆕 Ny KPI-periode oppdaget! (Gammel: '{last_seen}', Ny: '{latest_tid}')")

    # 3. Hent lenke fra RSS-feed
    link = "https://www.ssb.no/priser-og-prisindekser/konsumpriser/statistikk/konsumprisindeksen"
    try:
        req = urllib.request.Request(RSS_URL, headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req) as response:
            xml_data = response.read()
        root = ET.fromstring(xml_data)
        item = root.find('.//item')
        if item is not None:
            link = item.findtext('link', link).strip()
    except Exception as e:
        print(f"⚠️ RSS-lesing feilet: {e}")

    # 4. Formater teksten til Slack
    prev_m, curr_m = parse_months(latest_tid)

    news_text = (
        f"Prisene {verb_word(xx)} med {abs_fmt(xx)} % siste tolv måneder, viser tall fra SSB. "
        f"Prisen på matvarer {verb_word(zz)} med {abs_fmt(zz)} % i samme periode. "
        f"Fra {prev_m} til {curr_m} {verb_word(zz_mnd)} matvareprisene med {abs_fmt(zz_mnd)} %. "
        f"Kjerneinflasjonen siste 12 måneder var {fmt_num(yy)} prosent."
    )

    slack_text = (
        f"📈 *Nye tall fra SSB: Konsumprisindeksen ({latest_tid})*\n\n"
        f"{news_text}\n\n"
        f"• KPI Total (siste 12 mnd): *{fmt_num(xx)}%*\n"
        f"• Matvarer (siste 12 mnd): *{fmt_num(zz)}%*\n"
        f"• Matvarer (fra forrige måned): *{fmt_num(zz_mnd)}%*\n"
        f"• Kjerneinflasjon / KPI-JAE (siste 12 mnd): *{fmt_num(yy)}%*\n\n"
        f"👉 <{link}|Les hele rapporten hos SSB>"
    )

    # 5. Send meldingen til Slack
    print("📤 Sender melding til Slack...")
    payload = json.dumps({"text": slack_text}).encode('utf-8')
    slack_req = urllib.request.Request(
        slack_url,
        data=payload,
        headers={'Content-Type': 'application/json'}
    )
    
    try:
        with urllib.request.urlopen(slack_req) as response:
            if response.status in (200, 204):
                print("🎉 SUKSESS! KPI-melding ble sendt til Slack.")
                with open(STATE_FILE, "w") as f:
                    f.write(latest_tid)
            else:
                print(f"❌ Slack returnerte HTTP status: {response.status}")
                sys.exit(1)
    except Exception as e:
        print(f"❌ Feil ved sending til Slack: {e}")
        sys.exit(1)

if __name__ == "__main__":
    main()
