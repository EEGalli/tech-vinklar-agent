"""
ENISA — Europeiska unionens cybersäkerhetsbyrå.
RSS-feed med nyheter, rapporter, events. Allt är per definition cyber-relevant
så vi tar med allt men låter AI bedöma relevansnivå.
"""
import re
import requests
import xml.etree.ElementTree as ET

RSS_URL = "https://www.enisa.europa.eu/rss.xml"


def _clean_html(text: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", text)).strip()


def fetch_all() -> list[dict]:
    results = []
    try:
        resp = requests.get(RSS_URL, headers={"User-Agent": "Mozilla/5.0"}, timeout=15)
        resp.raise_for_status()
        root = ET.fromstring(resp.content)
        for item in root.findall(".//item"):
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
    except Exception as e:
        print(f"ENISA error: {e}")
    return results
