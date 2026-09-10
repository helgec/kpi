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

def get_kpi_metrics():
    """Henter KPI Total, Matvarer og KPI-JAE i én operasjon fra kildetabell 14704."""
    print("🔍 Henter nøkkeltall fra Tabell 14704...")
    meta = fetch_json("https://data.ssb.no/api/v0/no/table/14704")
    vars_list = meta["variables"]
    
    cnt_var = next(v for v in vars_list if v["code"] == "ContentsCode")
    tid_var = next(v for v in vars_list if v["code"] == "Tid")
    grp_var = next(v for v in vars_list if v["code"] not in ["ContentsCode", "Tid"])
    
    # Finn koder i Tabell 14704
    total_code = find_code(grp_var, ["totalindeks", "i alt", "kpi total"]) or grp_var["values"][0]
    mat_code = find_code(grp_var, ["matvarer og alkoholfrie", "matvarer"]) or grp_var["values"][1]
    jae_code = find_code(grp_var, ["(kpi-jae)", "kpi-jae"]) or grp_var["values"][-1]
    
    m12_code = find_code(cnt_var, ["12-måned", "tolv", "12 mnd"]) or cnt_var["values"][-1]
    latest_tid = tid_var["values"][-1]
    
    q = {
        "query": [
            {"code": grp_var["code"], "selection": {"filter": "item", "values": [total_code, mat_code, jae_code]}},
            {"code": cnt_var["code"], "selection": {"filter": "item", "values": [m12_code]}},
            {"code": tid_var["code"], "selection": {"filter": "item", "values": [latest_tid]}}
        ],
        "response": {"format": "json-stat2"}
    }
    
    res = fetch_json("https://data.ssb.no/api/v0/no/table/14704", q)
    vals = res.get("value", [])
    
    xx, zz, yy = None, None, None
    if len(vals) >= 3:
        xx = vals[0]  # Total
        zz = vals[1]  # Matvarer og alkoholfrie drikkevarer
        yy = vals[2]  # KPI-JAE
        print(f"✅ Hentet fra tabell 14704 ({latest_tid}): Total={xx}%, Mat={zz}%, KPI-JAE={yy}%")
        
    return xx, zz, yy, latest_tid

def main():
    slack_url = os.environ.get("SLACK_WEBHOOK_URL", "").strip()
    if not slack_url:
        print("❌ CRITICAL ERROR: Finner ikke SLACK_WEBHOOK_URL som miljøvariabel!")
        sys.exit(1)

    # 1. Hent tallene fra SSBs API
    try:
        xx, zz, yy, latest_tid = get_kpi_metrics()
        
        if not latest_tid or xx is None or zz is None or yy is None:
            raise ValueError("Klarte ikke hente alle tre nøkkeltallene fra tabell 14704.")
            
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
    slack_text = (
        f"📈 *Nye tall fra SSB: Konsumprisindeksen ({latest_tid})*\n\n"
        f"• KPI Total (siste 12 mnd): *{fmt_num(xx)}%*\n"
        f"• Matvarer og alkoholfri drikke (siste 12 mnd): *{fmt_num(zz)}%*\n"
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
