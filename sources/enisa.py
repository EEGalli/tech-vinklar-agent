"""
ENISA — Europeiska unionens cybersäkerhetsbyrå.
RSS-feed med nyheter, rapporter, events. Allt är per definition cyber-relevant
så vi tar med allt men låter AI bedöma relevansnivå.
"""
import re
import requests
import defusedxml.ElementTree as ET  # skyddar mot XXE + billion-laughs i feeden

RSS_URL = "https://www.enisa.europa.eu/rss.xml"


def _clean_html(text: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", text)).strip()


def fetch_all() -> list[dict]:
    """Hämtar ENISA-flödet. Skiljer mellan tre olika typer av problem så att
    en buggig källa inte ser ut som 'lugn vecka' i loggen:
      1. HTTP-fel (nätverk, 5xx, timeout) → loggas tydligt
      2. Trasig XML från servern → loggas tydligt
      3. RSS-svar men 0 items → varning, kan tyda på ändrad struktur"""
    results = []
    raw_item_count = 0
    try:
        try:
            resp = requests.get(RSS_URL, headers={"User-Agent": "Mozilla/5.0"}, timeout=30)
            resp.raise_for_status()
        except requests.exceptions.Timeout:
            print(f"  ⚠ ENISA timeout (30s) — feed-servern svarade inte", flush=True)
            return results
        except requests.exceptions.RequestException as e:
            print(f"  ⚠ ENISA HTTP-fel: {e}", flush=True)
            return results
        try:
            root = ET.fromstring(resp.content)
        except ET.ParseError as e:
            print(f"  ⚠ ENISA: feeden gav trasig XML ({e})", flush=True)
            return results
        for item in root.findall(".//item"):
            raw_item_count += 1
            title = item.findtext("title", "").strip()
            link = item.findtext("link", "").strip()
            desc = _clean_html(item.findtext("description", ""))
            pub_date = item.findtext("pubDate", "").strip()
            if not title:
                continue
            results.append({
                "source": "ENISA",
                "type": "EU cybersäkerhet",
                "title": title,
                "date": pub_date[:16] if pub_date else "",
                "committee": "ENISA (EU:s cybersäkerhetsbyrå)",
                "url": link,
                "summary": desc[:400],
                "doc_id": link,
            })
        if raw_item_count == 0:
            print(f"  ⚠ ENISA: feeden svarade men 0 items hittades — har strukturen ändrats?", flush=True)
    except Exception as e:
        print(f"  ⚠ ENISA okänt fel: {type(e).__name__}: {e}", flush=True)
    return results
