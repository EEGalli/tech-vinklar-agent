"""
Tech-policy-nyheter — externa medier/NGOs som täcker EU-policy-strider
som officiella institutionsfeeds ofta missar (Chat Control/CSAM, AI Omnibus, etc.).

Inte officiella källor, men fångar viktiga policy-strider tidigt när de
diskuteras i media innan formella dokument publiceras.
"""
import re
import requests
import xml.etree.ElementTree as ET
from config import TECH_KEYWORDS

FEEDS = [
    ("EDRi", "European Digital Rights (NGO)", "https://edri.org/feed/"),
    ("Politico EU Tech", "Politico Europe — tech-policy", "https://www.politico.eu/section/technology/feed/"),
]


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", text)).strip()


def _is_tech_relevant(text: str) -> bool:
    text_lower = text.lower()
    return any(kw.lower() in text_lower for kw in TECH_KEYWORDS)


def fetch_all() -> list[dict]:
    """Hämtar tech-policy-nyheter från EDRi + Politico EU.
    Bägge är hyfsat snäva på tech, men vi tech-keyword-filtrerar ändå."""
    results = []
    for short, full, url in FEEDS:
        try:
            resp = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=10)
            resp.raise_for_status()
            root = ET.fromstring(resp.content)
        except Exception as e:
            print(f"  Tech-news {short}: {e}")
            continue

        for item in root.findall(".//item"):
            title = item.findtext("title", "").strip()
            link = item.findtext("link", "").strip()
            desc = _clean(item.findtext("description", ""))
            pub_date = item.findtext("pubDate", "").strip()

            if not title:
                continue

            # Tech-keyword-filter — vissa Politico-artiklar är ren politik
            if not _is_tech_relevant(f"{title} {desc}"):
                continue

            results.append({
                "source": short,
                "type": "Tech-policy-nyhet",
                "title": title,
                "date": pub_date[:16] if pub_date else "",
                "committee": full,
                "url": link,
                "summary": desc[:400],
                "doc_id": link,
            })
    return results
