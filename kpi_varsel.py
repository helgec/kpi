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
    """Henter XX (KPI total), ZZ (Matvarer/drikke) og YY (KPI-JAE) fra tabeller."""
    xx, zz, yy, latest_tid = None, None, None, None
    
    # 1. Hent XX (Total) og ZZ (Matvarer og alkoholfrie drikkevarer) fra Tabell 14700 / 08183
    tables_main = ["14700", "08183"]
    for table_id in tables_main:
        try:
            print(f"🔍 Prøver tabell {table_id} for KPI Total og Matvarer...")
            meta = fetch_json(f"https://data.ssb.no/api/v0/no/table/{table_id}")
            vars_list = meta["variables"]
            
            cnt_var = next(v for v in vars_list if v["code"] == "ContentsCode")
            tid_var = next(v for v in vars_list if v["code"] == "Tid")
            grp_var = next(v for v in vars_list if v["code"] not in ["ContentsCode", "Tid"])
            
            total_code = find_code(grp_var, ["00 totalindeks", "00 i alt", "totalindeks"]) or "00"
            
            # Presist søk for å treffe 01 (Matvarer og alkoholfrie) og IKKE 01.1 (Kun mat)
            mat_code = find_code(grp_var, ["matvarer og alkoholfrie", "01 matvarer og"]) or "01"
            
            m12_code = find_code(cnt_var, ["12-måned", "tolv", "12 mnd"]) or cnt_var["values"][-1]
            latest_tid = tid_var["values"][-1]
            
            q = {
                "query": [
                    {"code": grp_var["code"], "selection": {"filter": "item", "values": [total_code, mat_code]}},
                    {"code": cnt_var["code"], "selection": {"filter": "item", "values": [m12_code]}},
                    {"code": tid_var["code"], "selection": {"filter": "item", "values": [latest_tid]}}
                ],
                "response": {"format": "json-stat2"}
            }
            
            res = fetch_json(f"https://data.ssb.no/api/v0/no/table/{table_id}", q)
            vals = res.get("value", [])
            if len(vals) >= 2:
                xx = vals[0]  # Total
                zz = vals[1]  # Matvarer og alkoholfrie drikkevarer
                print(f"✅ Hentet XX={xx} og ZZ={zz} fra tabell {table_id} ({latest_tid})")
                break
        except Exception as e:
            print(f"⚠️ Kunne ikke hente fra tabell {table_id}: {e}")

    # 2. Hent YY (KPI-JAE) fra Tabell 14706 / 14708 / 14704 / 08184
    tables_jae = ["14706", "14708", "14704", "08184"]
    for table_id in tables_jae:
        try:
            print(f"🔍 Prøver tabell {table_id} for KPI-JAE...")
            meta = fetch_json(f"https://data.ssb.no/api/v0/no/table/{table_id}")
            vars_list = meta["variables"]
            
            jae_cnt_var = next(v for v in vars_list if v["code"] == "ContentsCode")
            jae_tid_var = next(v for v in vars_list if v["code"] == "Tid")
            jae_grp_var = next(v for v in vars_list if v["code"] not in ["ContentsCode", "Tid"])
            
            # Eksakt søk på "(kpi-jae)" for å unngå feiltreff på "KPI-JE"
            jae_code = find_code(jae_grp_var, ["(kpi-jae)"])
            if not jae_code:
                continue
                
            jae_m12_code = find_code(jae_cnt_var, ["12-måned", "tolv", "12 mnd"]) or jae_cnt_var["values"][-1]
            latest_tid_jae = jae_tid_var["values"][-1]
            
            q = {
                "query": [
                    {"code": jae_grp_var["code"], "selection": {"filter": "item", "values": [jae_code]}},
                    {"code": jae_cnt_var["code"], "selection": {"filter": "item", "values": [jae_m12_code]}},
                    {"code": jae_tid_var["code"], "selection": {"filter": "item", "values": [latest_tid_jae]}}
                ],
                "response": {"format": "json-stat2"}
            }
            
            res = fetch_json(f"https://data.ssb.no/api/v0/no/table/{table_id}", q)
            vals = res.get("value", [])
            if vals and vals[0] is not None:
                yy = vals[0]
                if not latest_tid:
                    latest_tid = latest_tid_jae
                print(f"✅ Hentet YY={yy} fra tabell {table_id}")
                break
        except Exception as e:
            print(f"⚠️ Kunne ikke hente KPI-JAE fra tabell {table_id}: {e}")

    return xx, zz, yy, latest_tid

def main():
    slack_url = os.environ.get("SLACK_WEBHOOK_URL", "").strip()
    if not slack_url:
        print("❌ CRITICAL ERROR: Finner ikke SLACK_WEBHOOK_URL som miljøvariabel!")
        sys.exit(1)

    # 1. Hent tallene fra SSBs API
    try:
        xx, zz, yy, latest_tid = get_kpi_metrics()
        
        if not latest_tid:
            raise ValueError("Klarte ikke hente gyldig periode (Tid) fra noen SSB-tabeller.")
            
        print(f"✅ Hentet KPI-tall for {latest_tid}: Total={xx}%, Mat/drikke={zz}%, KPI-JAE={yy}%")
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
        f"• Mat og alkoholfri drikke (siste 12 mnd): *{fmt_num(zz)}%*\n"
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
