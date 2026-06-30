"""
Svenska myndigheter med tech-policy-relevans — IMY (datatillsyn), MSB (cyber).
Departement täcker vi via regeringen.se redan.
"""
import re
import requests
import xml.etree.ElementTree as ET
from config import TECH_KEYWORDS

AGENCIES = [
    ("IMY", "Integritetsskyddsmyndigheten (datatillsyn, GDPR, AI Act-marknadskontroll)",
     "https://www.imy.se/nyheter/rss"),
    ("MSB", "Myndigheten för samhällsskydd och beredskap / Civilt försvar (cyber, NIS2)",
     "https://www.mcf.se/sv/rss-floden/rss-alla-nyheter-fran-myndigheten-for-civilt-forsvar"),
]


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", text)).strip()


def _is_tech_relevant(text: str) -> bool:
    text_lower = text.lower()
    return any(kw.lower() in text_lower for kw in TECH_KEYWORDS)


def fetch_all() -> list[dict]:
    """Hämtar tech-relevanta nyheter från svenska myndigheter med RSS.
    IMY är default-tech (alla dataskyddsbeslut räknas), MSB tech-keyword-filtreras."""
    results = []
    for short, full, url in AGENCIES:
        try:
            resp = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=10)
            resp.raise_for_status()
            root = ET.fromstring(resp.content)
        except Exception as e:
            print(f"  Svenska myndigheter {short}: {e}")
            continue

        for item in root.findall(".//item"):
            title = item.findtext("title", "").strip()
            link = item.findtext("link", "").strip()
            desc = _clean(item.findtext("description", ""))
            pub_date = item.findtext("pubDate", "").strip()
            if not title:
                continue

            # IMY = alltid tech-relevant (datatillsyn). MSB = kräver tech-keyword.
            if short != "IMY" and not _is_tech_relevant(f"{title} {desc}"):
                continue

            results.append({
                "source": short,
                "type": "Svensk myndighet",
                "title": title,
                "date": pub_date[:16] if pub_date else "",
                "committee": full,
                "url": link,
                "summary": desc[:400],
                "doc_id": link,
            })
    return results
