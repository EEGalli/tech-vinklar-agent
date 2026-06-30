"""
Hämtar tech-relevanta EU-ärenden via EP-utskottens RSS och Cellar SPARQL.

EUR-Lex blockerar bots via WAF. Använder istället:
1. EP:s officiella utskotts-RSS (last-news-committees) — filtrerad på tech-utskott
2. Cellar SPARQL endpoint (publications.europa.eu) — för COM-dokument

Tech-relevanta utskott i EP:
- ITRE: Industry, Research and Energy
- IMCO: Internal Market and Consumer Protection
- LIBE: Civil Liberties (GDPR, AI Act)
- TRAN: Transport (connected vehicles, digital infrastructure)
- ECON: Economic (fintech, crypto)
"""
import re
import requests
import xml.etree.ElementTree as ET
from config import TECH_KEYWORDS

# EP utskotts-RSS (verifierat fungerande)
EP_COMMITTEES_RSS = "http://www.europarl.europa.eu/rss/doc/last-news-committees/en.xml"

# EU-kommissionens digital-nyhetsrum (Digital Strategy newsroom)
# Publicerar AI Act, DSA, DMA, Chips Act, Data Act etc. — kärnan i EU:s tech-politik
COMMISSION_DIGITAL_RSS = "https://digital-strategy.ec.europa.eu/en/rss.xml"

# Tech-relevanta utskott (body-kategorin i RSS-flödet)
TECH_COMMITTEES = {"ITRE", "IMCO", "LIBE", "TRAN", "ECON", "JURI", "AIDA"}

# Cellar SPARQL endpoint (Publications Office)
CELLAR_SPARQL = "https://publications.europa.eu/webapi/rdf/sparql"


def _clean_html(text: str) -> str:
    return re.sub(r"<[^>]+>", " ", text).strip()


def _is_tech_relevant(text: str) -> bool:
    text_lower = text.lower()
    return any(kw.lower() in text_lower for kw in TECH_KEYWORDS)


# Slänga events/jobb/marknadsföring — användaren vill ha policy, inte happenings.
# Synkad med sources/eu_agencies.py:_EXCLUDE_PATTERNS.
_EXCLUDE_PATTERNS = (
    "workshop", "conference", "summit", "webinar", "training session",
    "save the date", "hackathon", "info session", "info day",
    "registration is open", "join us", "exhibition",
    "vacancy", "recruitment", "we are hiring", "traineeship", "internship",
    "brochure", "leaflet", "rollup", "press kit", "media kit",
    "newsletter ", "composition of the",
)


def _is_excluded(text: str) -> bool:
    t = text.lower()
    return any(p in t for p in _EXCLUDE_PATTERNS)


def _normalize_ep_title(title: str) -> str:
    """Tar bort RSS-prefix ('Highlights - ', 'Newsletters - ', 'Latest news - ')
    och kommittéförtydliganden för att jämföra innehåll."""
    t = title
    for prefix in ("Highlights - ", "Newsletters - ", "Latest news - "):
        if t.startswith(prefix):
            t = t[len(prefix):]
            break
    # Ta bort kommittéreferens i slutet: "... - Committee on X"
    t = re.sub(r"\s*-\s*(Committee on|Subcommittee on|Special Committee).*$", "", t, flags=re.IGNORECASE)
    return " ".join(t.lower().split())


def fetch_ep_committee_news() -> list[dict]:
    """
    Hämtar senaste nyheter från EP-utskotten via RSS.
    Behåller Highlights, Newsletters och Latest news, men deduplicerar på
    innehåll (efter att RSS-prefix och kommittésuffix tagits bort).
    """
    results = []
    seen_content_keys: set[str] = set()
    try:
        resp = requests.get(EP_COMMITTEES_RSS, timeout=15)
        resp.raise_for_status()
        root = ET.fromstring(resp.content)

        for item in root.findall(".//item"):
            title = item.findtext("title", "").strip()
            link = item.findtext("link", "").strip()
            desc = _clean_html(item.findtext("description", ""))
            pub_date = item.findtext("pubDate", "").strip()

            if not title:
                continue

            # Dedupe på normaliserat innehåll — fångar "Highlights - X" och
            # "Latest news - X" som handlar om samma sak
            content_key = _normalize_ep_title(title)
            if content_key and content_key in seen_content_keys:
                continue
            seen_content_keys.add(content_key)

            committees_in_item = set()
            for cat in item.findall("category"):
                if cat.get("domain") == "body":
                    committees_in_item.add(cat.text or "")

            # Slänga events/jobb/marknadsföring — vi vill ha policy, inte happenings
            if _is_excluded(f"{title} {desc}"):
                continue

            committee_match = bool(committees_in_item & TECH_COMMITTEES)
            keyword_match = _is_tech_relevant(f"{title} {desc}")

            if committee_match or keyword_match:
                committee_names = ", ".join(committees_in_item) if committees_in_item else "EP-utskott"
                results.append({
                    "source": "EU-parlamentet",
                    "type": "EP/Utskott",
                    "title": title,
                    "date": pub_date[:16] if pub_date else "",
                    "committee": committee_names,
                    "url": link,
                    "summary": desc[:400],
                    "doc_id": link,
                })
    except ET.ParseError as e:
        print(f"EP committee RSS parse error: {e}")
    except Exception as e:
        print(f"EP committee RSS error: {e}")

    return results


def fetch_cellar_recent_com_docs() -> list[dict]:
    """
    Hämtar nya COM-dokument (kommissionsförslag) via Cellar SPARQL.
    Filtrerar på tech-relevanta ämnen från 2025 och framåt.
    """
    # SPARQL-fråga: Hämta senaste COM-dokument om digital/AI/cyber-ämnen
    sparql_query = """
    PREFIX cdm: <http://publications.europa.eu/ontology/cdm#>
    PREFIX skos: <http://www.w3.org/2004/02/skos/core#>
    PREFIX dc: <http://purl.org/dc/elements/1.1/>
    PREFIX xsd: <http://www.w3.org/2001/XMLSchema#>

    SELECT DISTINCT ?work ?title ?date ?type
    WHERE {
      ?work cdm:work_date_document ?date .
      ?work cdm:resource_legal_comment_internal ?title .
      FILTER(?date >= "2025-01-01"^^xsd:date)
      FILTER(
        CONTAINS(LCASE(STR(?title)), "artificial intelligence") ||
        CONTAINS(LCASE(STR(?title)), "digital") ||
        CONTAINS(LCASE(STR(?title)), "cybersecurity") ||
        CONTAINS(LCASE(STR(?title)), "data act") ||
        CONTAINS(LCASE(STR(?title)), "semiconductor") ||
        CONTAINS(LCASE(STR(?title)), "platform")
      )
    }
    ORDER BY DESC(?date)
    LIMIT 20
    """

    results = []
    try:
        resp = requests.get(
            CELLAR_SPARQL,
            params={
                "query": sparql_query,
                "format": "application/sparql-results+json",
            },
            headers={"Accept": "application/sparql-results+json"},
            timeout=20,
        )
        resp.raise_for_status()
        data = resp.json()
        bindings = data.get("results", {}).get("bindings", [])

        for b in bindings:
            work_uri = b.get("work", {}).get("value", "")
            title = b.get("title", {}).get("value", "")
            date = b.get("date", {}).get("value", "")[:10]

            # SPARQL-frågan filtrerar redan på tech-ord (rad 152-158). Vi gör inte
            # ett andra Python-filter — det krävde svenska nyckelord och slängde
            # engelska COM-dokument om AI Act, Data Act, Cybersecurity etc.
            if title:
                results.append({
                    "source": "EU-kommissionen",
                    "type": "EC/COM-dokument (Cellar)",
                    "title": title,
                    "date": date,
                    "committee": "EU-kommissionen",
                    "url": work_uri,
                    "summary": "",
                    "doc_id": work_uri,
                })
    except Exception as e:
        print(f"Cellar SPARQL error: {e}")

    return results


def fetch_commission_digital_news() -> list[dict]:
    """Hämtar EU-kommissionens digital-nyhetsrum via RSS.
    Detta är kärnan i EU:s tech-politik: AI Act, DSA, DMA, Chips Act, utredningar."""
    results = []
    try:
        resp = requests.get(COMMISSION_DIGITAL_RSS, headers={"User-Agent": "Mozilla/5.0"}, timeout=15)
        resp.raise_for_status()
        root = ET.fromstring(resp.content)

        for item in root.findall(".//item"):
            title = item.findtext("title", "").strip()
            link = item.findtext("link", "").strip()
            desc = _clean_html(item.findtext("description", ""))
            pub_date = item.findtext("pubDate", "").strip()

            if not title:
                continue

            # Skippa events/funding helt — vi vill ha beslut/policy, inte happenings
            if "/events/" in link or "/funding/" in link:
                continue
            if _is_excluded(f"{title} {desc}"):
                continue

            # Grov kategori
            category = "Nyhet"
            if "/consultations/" in link:
                category = "Konsultation"

            # Nästan allt här är tech-relevant, men filtrera bort rena event/funding-poster
            if category in ("Event", "Finansiering"):
                if not _is_tech_relevant(f"{title} {desc}"):
                    continue

            results.append({
                "source": "EU-kommissionen",
                "type": f"Digital Strategy — {category}",
                "title": title,
                "date": pub_date[:16] if pub_date else "",
                "committee": "EU-kommissionen (digital-strategy)",
                "url": link,
                "summary": desc[:500],
                "doc_id": link,
            })
    except ET.ParseError as e:
        print(f"Commission Digital RSS parse error: {e}")
    except Exception as e:
        print(f"Commission Digital RSS error: {e}")

    return results


def fetch_all() -> list[dict]:
    """Hämtar alla tech-relevanta EU-institutionsdokument (utom EP Open Data som hanteras separat)."""
    items = fetch_ep_committee_news() + fetch_commission_digital_news() + fetch_cellar_recent_com_docs()

    # Deduplicera
    seen = set()
    unique = []
    for item in items:
        key = item.get("doc_id", item.get("url", ""))
        if key and key not in seen:
            seen.add(key)
            unique.append(item)

    return unique
