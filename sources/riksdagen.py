"""
Hämtar tech-relevanta ärenden från Riksdagens öppna API.
Datakälla: data.riksdagen.se
"""
import time
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from datetime import datetime, timedelta
from config import TECH_KEYWORDS, TECH_RELEVANT_COMMITTEES, LOOKAHEAD_DAYS

BASE_URL = "https://data.riksdagen.se"
WEBB_BASE = "https://www.riksdagen.se/sv/dokument-och-lagar/dokument"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
}

def _session() -> requests.Session:
    """Skapar en requests-session med automatisk retry och fördröjning."""
    s = requests.Session()
    retry = Retry(total=3, backoff_factor=2, status_forcelist=[429, 500, 502, 503])
    s.mount("https://", HTTPAdapter(max_retries=retry))
    s.headers.update(HEADERS)
    return s

SESSION = _session()

# Dokumenttyp → URL-segment på riksdagen.se
DOC_TYPE_PATH = {
    "prop": "proposition",
    "mot":  "motion",
    "skr":  "skrivelse",
    "bet":  "betankande",
    "sou":  "statens-offentliga-utredningar",
    "ds":   "departementsserien",
    "fpm":  "fakta-pm-om-eu-forslag",
    "ip":   "interpellation",
    "fr":   "skriftliga-fragor",
}


def _is_published(url: str) -> bool:
    """Kontrollerar att dokumentet faktiskt är publicerat (inte bara reserverat i API:t).
    Använder HEAD-anrop + HTTP-status — den tidigare text-checken på 'inte publicerat'
    matchade Riksdagens språkresurser som finns inbäddade på ALLA sidor (även publicerade),
    vilket fick alla URL:er att tömmas felaktigt."""
    try:
        resp = SESSION.head(url, timeout=20, allow_redirects=True)
        return resp.status_code == 200
    except Exception:
        return False


def _riksdagen_url(dok_id: str, doc_type: str, dokument_url_html: str = "") -> str:
    """Returnerar en länk till dokumentet. Använder API:ts URL om tillgänglig."""
    if dokument_url_html:
        url = dokument_url_html.strip()
        if url.startswith("//"):
            url = "https:" + url
        # Lägg till .html om det saknas
        if not url.endswith(".html"):
            url += ".html"
        return url
    # Fallback: bygg URL från dok_id
    path_segment = DOC_TYPE_PATH.get(doc_type.lower(), "proposition")
    return f"{WEBB_BASE}/{path_segment}/{dok_id.upper()}/"


def _is_tech_relevant(text: str) -> bool:
    text_lower = text.lower()
    return any(kw.lower() in text_lower for kw in TECH_KEYWORDS)


# Viktiga återkommande dokument som alltid ska inkluderas (AI avgör relevansen)
ALWAYS_INCLUDE_PATTERNS = [
    "vårproposition",
    "vårändringsbudget",
    "ändringsbudget",
    "budgetproposition",
    "ekonomiska vårpropositionen",
]

def _is_always_include(text: str) -> bool:
    text_lower = text.lower()
    return any(p in text_lower for p in ALWAYS_INCLUDE_PATTERNS)


def fetch_upcoming_debates() -> list[dict]:
    """Hämtar kommande debatter och beslut från Riksdagens kalender (XML-format)."""
    today = datetime.today()
    end_date = today + timedelta(days=LOOKAHEAD_DAYS)

    url = f"{BASE_URL}/kalender/"
    params = {
        "from": today.strftime("%Y-%m-%d"),
        "tom": end_date.strftime("%Y-%m-%d"),
        "utformat": "json",
        "sz": 100,
    }

    try:
        resp = requests.get(url, params=params, timeout=10)
        resp.raise_for_status()
        # Kalendern returnerar XML/HTML — vi hämtar XML-varianten
    except Exception:
        pass

    # Kalender-API:et stöder inte JSON, använd dokumentlista istället
    return fetch_recent_propositioner() + fetch_upcoming_voteringar()


def fetch_recent_propositioner(days_back: int = 30) -> list[dict]:
    """Hämtar nya propositioner, motioner och skrivelser med tech-relevans.
    Gör EN request per typ med 5s paus för att undvika rate-limiting."""
    from_date = (datetime.today() - timedelta(days=days_back)).strftime("%Y-%m-%d")

    results = []
    for doc_type in ["prop", "mot", "skr", "fpm", "ip", "sou"]:
        url = f"{BASE_URL}/dokumentlista/"
        params = {
            "utformat": "json",
            "doktyp": doc_type,
            "from": from_date,
            "sz": 50,
            "sort": "datum",
            "sortorder": "desc",
        }
        # Exponential backoff på Connection reset: 5s → 30s → 90s
        last_err = None
        for attempt, delay in enumerate([5, 30, 90]):
            try:
                time.sleep(delay)
                resp = SESSION.get(url, params=params, timeout=30)
                resp.raise_for_status()
                data = resp.json()
                docs = data.get("dokumentlista", {}).get("dokument", []) or []
                for doc in docs:
                    title = doc.get("titel", "")
                    summary = doc.get("notis", "")
                    text = f"{title} {summary}"
                    if _is_tech_relevant(text) or _is_always_include(title):
                        dok_id = doc.get("dok_id", "")
                        item_url = _riksdagen_url(dok_id, doc_type, doc.get("dokument_url_html", ""))
                        # Använd URL som backup-doc_id om dok_id saknas (händer för
                        # nya/preliminära ärenden). Förut räknades alla tomma-dok_id-items
                        # som "samma" och dedupering tog bort dem felaktigt.
                        item_doc_id = dok_id or item_url or title
                        results.append({
                            "source": "Riksdagen",
                            "type": f"Riksdagen/{doc_type.upper()}",
                            "title": title,
                            "date": doc.get("datum", ""),
                            "committee": doc.get("organ", ""),
                            "url": item_url,
                            "summary": summary[:400] if summary else "",
                            "doc_id": item_doc_id,
                        })
                last_err = None
                break  # lyckades, hoppa ur retry-loop
            except Exception as e:
                last_err = e
                if attempt < 2:
                    print(f"Riksdagen {doc_type}: försök {attempt+1} misslyckades ({str(e)[:80]}), väntar innan retry...")
        if last_err:
            # Fallback: prova RSS-endpointen om JSON misslyckas — samma data, annan format
            print(f"Riksdagen {doc_type}: JSON misslyckades, försöker RSS-fallback...")
            try:
                rss_items = _fetch_via_rss(doc_type)
                results.extend(rss_items)
                print(f"  → RSS gav {len(rss_items)} items")
            except Exception as e:
                print(f"  → RSS också misslyckades: {e}")

    return results


def _fetch_via_rss(doc_type: str) -> list[dict]:
    """Fallback: hämta dokument via RSS-feeden när JSON blockeras.
    Samma data, annan format — använder XML-parsing istället."""
    import xml.etree.ElementTree as ET
    url = f"{BASE_URL}/dokumentlista/?utformat=rss&doktyp={doc_type}&sz=50"
    resp = SESSION.get(url, timeout=20)
    resp.raise_for_status()
    root = ET.fromstring(resp.content)
    results = []
    for item in root.findall(".//item"):
        title = item.findtext("title", "").strip()
        link = item.findtext("link", "").strip()
        pub_date = item.findtext("pubDate", "").strip()
        desc = item.findtext("description", "").strip()
        text = f"{title} {desc}"
        if _is_tech_relevant(text) or _is_always_include(title):
            # Extrahera dok_id ur länken (t.ex. .../_hd03252/ → HD03252)
            import re as _re
            m = _re.search(r"_(hd\d+|h\d+)/?$", link, _re.IGNORECASE)
            dok_id = m.group(1).upper() if m else link.rsplit("/", 1)[-1].upper()
            results.append({
                "source": "Riksdagen",
                "type": f"Riksdagen/{doc_type.upper()}",
                "title": title,
                "date": pub_date[:16] if pub_date else "",
                "committee": "",
                "url": link,
                "summary": desc[:400] if desc else "",
                "doc_id": dok_id,
            })
    return results


def fetch_upcoming_voteringar(days_ahead: int = LOOKAHEAD_DAYS) -> list[dict]:
    """Hämtar kommande voteringar (betänkanden som är redo för beslut)."""
    # Betänkanden (bet) nära beslut = kommande omröstningar
    from_date = datetime.today().strftime("%Y-%m-%d")
    to_date = (datetime.today() + timedelta(days=days_ahead)).strftime("%Y-%m-%d")

    url = f"{BASE_URL}/dokumentlista/"
    params = {
        "utformat": "json",
        "doktyp": "bet",
        "from": from_date,
        "sz": 50,
        "sort": "datum",
        "sortorder": "asc",
    }

    results = []
    try:
        time.sleep(5)
        resp = SESSION.get(url, params=params, timeout=20)
        resp.raise_for_status()
        data = resp.json()
        docs = data.get("dokumentlista", {}).get("dokument", []) or []
        for doc in docs:
            title = doc.get("titel", "")
            summary = doc.get("notis", "")
            organ = doc.get("organ", "")

            # Kolla om tech-relevant utskott ELLER tech-nyckelord
            committee_match = any(c.lower() in organ.lower() for c in TECH_RELEVANT_COMMITTEES)
            keyword_match = _is_tech_relevant(f"{title} {summary}")

            if committee_match or keyword_match:
                # Inkludera bara URL om dokumentet faktiskt är publicerat
                candidate_url = _riksdagen_url(doc.get("dok_id", ""), "bet", doc.get("dokument_url_html", "")) \
                    if doc.get("dokument_url_html") else ""
                doc_url = candidate_url if (candidate_url and _is_published(candidate_url)) else ""
                results.append({
                    "source": "Riksdagen",
                    "type": "Riksdagen/BET (kommande omröstning)",
                    "title": title,
                    "date": doc.get("datum", ""),
                    "committee": organ,
                    "url": doc_url,
                    "summary": summary[:400] if summary else "",
                    "doc_id": doc.get("dok_id", ""),
                })
    except Exception as e:
        print(f"Riksdagen voteringar error: {e}")

    return results


def fetch_all() -> list[dict]:
    """Hämtar alla tech-relevanta ärenden från Riksdagen."""
    items = fetch_recent_propositioner() + fetch_upcoming_voteringar()
    # Deduplicera på dok_id
    seen = set()
    unique = []
    for item in items:
        if item["doc_id"] not in seen:
            seen.add(item["doc_id"])
            unique.append(item)
    return unique
