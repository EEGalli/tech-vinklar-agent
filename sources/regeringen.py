"""
Hämtar pressmeddelanden från regeringen.se.
Scrapar HTML eftersom det inte finns någon öppen RSS/JSON-feed.
"""
import html as htmllib
import re
import time
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from config import TECH_KEYWORDS

BASE = "https://www.regeringen.se"

# Dokumenttyper vi vill hämta från regeringen.se
# (visningsnamn → listningssökväg)
DOC_TYPES = {
    "Pressmeddelande": "/pressmeddelanden/",
    "Regeringsuppdrag": "/regeringsuppdrag/",
    "Kommittédirektiv": "/rattsliga-dokument/kommittedirektiv/",
    "Lagrådsremiss": "/rattsliga-dokument/lagradsremiss/",
    "SOU": "/rattsliga-dokument/statens-offentliga-utredningar/",
    "Remiss": "/remisser/",
}

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
}


def _session() -> requests.Session:
    s = requests.Session()
    retry = Retry(total=3, backoff_factor=2, status_forcelist=[429, 500, 502, 503])
    s.mount("https://", HTTPAdapter(max_retries=retry))
    s.headers.update(HEADERS)
    return s


SESSION = _session()


def _strip(html_str: str) -> str:
    txt = re.sub(r"<[^>]+>", " ", html_str)
    txt = htmllib.unescape(txt)
    return re.sub(r"\s+", " ", txt).strip()


def _list_items(list_path: str, pages: int = 2) -> list[dict]:
    """Hämtar listning av dokument från regeringen.se (20 per sida).
    list_path: t.ex. '/pressmeddelanden/', '/regeringsuppdrag/', '/kommittedirektiv/'
    """
    # Lokalt regex — list_path används bara för URL, regexen matchar alla dokumenttyper
    item_re = re.compile(
        r'<a\s+href="(' + re.escape(list_path) + r'\d{4}/\d{2}/[^"]+/)">([^<]+)</a>'
        r'.*?<time datetime="(\d{4}-\d{2}-\d{2})">'
        r'.*?(?:fr&#xE5;n|från)(.*?)</p>',
        re.DOTALL,
    )

    items: list[dict] = []
    seen_urls: set[str] = set()
    for page in range(1, pages + 1):
        url = f"{BASE}{list_path}"
        params = {"p": page} if page > 1 else {}
        try:
            resp = SESSION.get(url, params=params, timeout=15)
            resp.raise_for_status()
        except Exception:
            continue
        for m in item_re.finditer(resp.text):
            path, title_raw, datum, dept_raw = m.group(1), m.group(2), m.group(3), m.group(4)
            full_url = f"{BASE}{path}"
            if full_url in seen_urls:
                continue
            seen_urls.add(full_url)
            title = htmllib.unescape(title_raw).strip()
            departments = [
                htmllib.unescape(a).strip()
                for a in re.findall(r'>([^<]+)</a>', dept_raw)
            ]
            departments = [d for d in departments if d]
            items.append({
                "title": title,
                "url": full_url,
                "date": datum,
                "committee": ", ".join(departments[-3:]),
            })
        time.sleep(1)
    return items


def _fetch_summary(url: str) -> str:
    """Hämtar meta description från en enskild pressmeddelande-sida."""
    try:
        time.sleep(0.8)
        resp = SESSION.get(url, timeout=10)
        resp.raise_for_status()
    except Exception:
        return ""
    m = re.search(r'<meta\s+name="description"\s+content="([^"]+)"', resp.text)
    if not m:
        return ""
    return htmllib.unescape(m.group(1)).strip()


def _is_tech_relevant(text: str) -> bool:
    text_lower = text.lower()
    return any(kw.lower() in text_lower for kw in TECH_KEYWORDS)


def _cached_urls() -> set[str]:
    """URL:er som redan ligger i analys-cachen — ingen summary-fetch behövs."""
    try:
        import memory as mem
        return set(mem.load_analysis_cache().keys())
    except Exception:
        return set()


def fetch_all() -> list[dict]:
    """Huvudfunktion: hämtar tech-relevanta dokument från regeringen.se.

    Optimering: items vars URL redan finns i analys-cachen hoppar över
    summary-fetchen eftersom AI-analysen kommer återanvändas ändå.
    """
    cached = _cached_urls()
    seen_urls: set[str] = set()
    results: list[dict] = []
    skipped_summary = 0

    for type_name, list_path in DOC_TYPES.items():
        items = _list_items(list_path, pages=1)
        for item in items:
            if item["url"] in seen_urls:
                continue
            title = item["title"]
            title_match = _is_tech_relevant(title)

            # Om URL:en redan är cachad: skippa summary-fetch helt.
            # AI kommer återanvända gammal analys, summary används inte.
            if item["url"] in cached:
                seen_urls.add(item["url"])
                skipped_summary += 1
                results.append({
                    "title": title,
                    "url": item["url"],
                    "date": item["date"],
                    "source": "Regeringen",
                    "type": type_name,
                    "committee": item["committee"],
                    "summary": "",
                })
                continue

            summary = _fetch_summary(item["url"])
            combined = f"{title} {summary}"
            if title_match or _is_tech_relevant(combined):
                seen_urls.add(item["url"])
                results.append({
                    "title": title,
                    "url": item["url"],
                    "date": item["date"],
                    "source": "Regeringen",
                    "type": type_name,
                    "committee": item["committee"],
                    "summary": summary,
                })

    if skipped_summary:
        print(f"    (regeringen: skippade {skipped_summary} summary-fetchar tack vare cache)")
    return results
