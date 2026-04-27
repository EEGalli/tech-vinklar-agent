"""
EU-byråer — samlat flöde från RSS-feeds.
ENISA hanteras separat i sources/enisa.py för historiska skäl, men inkluderas
även här så vi kan ta bort den specialfilen senare om vi vill konsolidera.
"""
import re
import requests
import xml.etree.ElementTree as ET
from config import TECH_KEYWORDS

# Lista över EU-byråer med fungerande RSS-feeds
AGENCIES = [
    ("EDPB",     "European Data Protection Board",            "https://www.edpb.europa.eu/rss.xml"),
    ("BEREC",    "Telekom-reglerare",                          "https://www.berec.europa.eu/rss.xml"),
    ("ESMA",     "Värdepappersmyndigheten",                   "https://www.esma.europa.eu/rss.xml"),
    ("Europol",  "EU:s polisbyrå",                             "https://www.europol.europa.eu/rss.xml"),
    ("EBA",      "Europeiska bankmyndigheten",                "https://www.eba.europa.eu/rss.xml"),
    ("EMA",      "Europeiska läkemedelsmyndigheten",          "https://www.ema.europa.eu/en/news.xml"),
    ("ACER",     "Europeiska energiregleringsbyrån",          "https://www.acer.europa.eu/rss.xml"),
    ("CEDEFOP",  "Yrkesutbildningscentret",                   "https://www.cedefop.europa.eu/rss.xml"),
    ("EUSPA",    "Europeiska rymdbyrån",                       "https://www.euspa.europa.eu/rss.xml"),
    ("Eurojust", "EU:s straffrättsliga samarbetsbyrå",        "https://www.eurojust.europa.eu/rss.xml"),
    ("EU-OSHA",  "Arbetsmiljöbyrån",                          "https://osha.europa.eu/en/rss.xml"),
]


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", text)).strip()


def _is_tech_relevant(text: str) -> bool:
    text_lower = text.lower()
    return any(kw.lower() in text_lower for kw in TECH_KEYWORDS)


def fetch_all() -> list[dict]:
    """Hämtar tech-relevanta nyheter från alla EU-byråer med RSS."""
    results = []
    for short, full, rss_url in AGENCIES:
        try:
            resp = requests.get(rss_url, headers={"User-Agent": "Mozilla/5.0"}, timeout=10)
            resp.raise_for_status()
            root = ET.fromstring(resp.content)
        except Exception as e:
            print(f"  EU-byrå {short}: {e}")
            continue

        for item in root.findall(".//item"):
            title = item.findtext("title", "").strip()
            link = item.findtext("link", "").strip()
            desc = _clean(item.findtext("description", ""))
            pub_date = item.findtext("pubDate", "").strip()

            if not title:
                continue

            # Filtrera bort uppenbart icke-tech (utom för rena cyber/data-byråer)
            always_tech = short in ("ENISA", "EDPB", "BEREC", "EUSPA")
            if not always_tech and not _is_tech_relevant(f"{title} {desc}"):
                continue

            results.append({
                "source": short,
                "type": "EU-byrå",
                "title": title,
                "date": pub_date[:16] if pub_date else "",
                "committee": full,
                "url": link,
                "summary": desc[:400],
                "doc_id": link,
            })

    return results
