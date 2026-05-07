"""
Enkelt minne — sparar varje körnings ärenden till en JSON-fil
så att nästa körning kan summera vad som hände igår / i veckan.
"""
import json
import os
import re
from datetime import datetime, date, timedelta


def _fix_url(url: str) -> str:
    """Migrerar gamla riksdagen www-URL:er till data.riksdagen.se-format."""
    if not url:
        return url
    # Konvertera www.riksdagen.se dokument-URL:er till data.riksdagen.se
    # Mönster: https://www.riksdagen.se/sv/dokument-och-lagar/dokument/{type}/{id}(_id)?/
    m = re.search(r'riksdagen\.se/sv/dokument-och-lagar/dokument/[^/]+/([^/]+?)(?:_\1)?/?$', url, re.IGNORECASE)
    if m:
        dok_id = m.group(1).upper()
        return f"https://data.riksdagen.se/dokument/{dok_id}.html"
    return url

MEMORY_FILE = os.path.join(os.path.dirname(__file__), ".agent_memory.json")
DATES_FILE = os.path.join(os.path.dirname(__file__), ".agent_dates.json")
CACHE_FILE = os.path.join(os.path.dirname(__file__), ".agent_analysis_cache.json")


def save(items: list[dict]) -> None:
    """Sparar dagens ärenden till minnet."""
    existing = _load_raw()
    today_str = date.today().isoformat()
    existing[today_str] = [
        {
            "title": i.get("title", ""),
            "source": i.get("source", ""),
            "type": i.get("type", ""),
            "url": i.get("url", ""),
            "date": i.get("date", ""),
            "analysis": i.get("analysis", {}),
        }
        for i in items
    ]
    # Behåll bara senaste 30 dagarna
    cutoff = (date.today() - timedelta(days=30)).isoformat()
    trimmed = {k: v for k, v in existing.items() if k >= cutoff}
    with open(MEMORY_FILE, "w", encoding="utf-8") as f:
        json.dump(trimmed, f, ensure_ascii=False, indent=2)


def save_analysis_cache(items: list[dict], max_age_days: int = 30) -> None:
    """Sparar ALLA analyserade items (inkl filtrerade icke-tech) till en cache.
    Nästa körning använder detta för att undvika att re-analysera samma URL:er.
    Keyed på URL med timestamp — items äldre än max_age_days rensas automatiskt."""
    print(f"  [save_analysis_cache] anropad med {len(items)} items, fil={CACHE_FILE}", flush=True)
    cache = load_analysis_cache()
    print(f"  [save_analysis_cache] befintlig cache hade {len(cache)} items", flush=True)
    today = date.today().isoformat()
    for item in items:
        url = item.get("url") or ""
        analysis = item.get("analysis")
        if not url or not analysis:
            continue
        # Cacha INTE timeout-fel eller andra felmeddelanden — de ska försökas igen
        tech_vinkel = analysis.get("tech_vinkel") or ""
        if tech_vinkel.startswith("Fel:") or analysis.get("relevans") == "okänd":
            continue
        cache[url] = {
            "analysis": analysis,
            "title": item.get("title", ""),
            "cached_at": today,
        }
    # Rensa poster äldre än max_age_days
    cutoff = (date.today() - timedelta(days=max_age_days)).isoformat()
    cache = {u: e for u, e in cache.items() if e.get("cached_at", "") >= cutoff}
    with open(CACHE_FILE, "w", encoding="utf-8") as f:
        json.dump(cache, f, ensure_ascii=False, indent=2)
    print(f"  [save_analysis_cache] skrev {len(cache)} items till disk", flush=True)


def load_analysis_cache() -> dict:
    """Läser analys-cachen. Returnerar {url: {analysis, title, cached_at}}."""
    if not os.path.exists(CACHE_FILE):
        return {}
    try:
        with open(CACHE_FILE, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def save_dates(items: list[dict]) -> None:
    """Sparar viktiga datum från AI-analysen till en separat fil.
    Dedupe på (datum + item title): ett kalenderinlägg per dokument per datum.
    Varianter av samma beskrivning skriver över den äldre."""
    existing = _load_dates_raw()
    for item in items:
        item_title = item.get("title", "")
        for d in item.get("analysis", {}).get("viktiga_datum", []) or []:
            datum = d.get("datum", "")
            beskrivning = d.get("beskrivning", "")
            if not datum or not beskrivning:
                continue
            try:
                datetime.strptime(datum, "%Y-%m-%d")
            except ValueError:
                continue
            if datum not in existing:
                existing[datum] = []
            entry = {
                "beskrivning": beskrivning,
                "title": item_title,
                "url": item.get("url", ""),
                "arende": item.get("analysis", {}).get("arende", ""),
            }
            match = next((e for e in existing[datum] if e.get("title") == item_title), None)
            if match is not None:
                match.update(entry)
            else:
                existing[datum].append(entry)
    with open(DATES_FILE, "w", encoding="utf-8") as f:
        json.dump(existing, f, ensure_ascii=False, indent=2)


def get_important_dates() -> dict:
    """Returnerar alla sparade viktiga datum, sorterade.
    Expanderar utskottsförkortningar och prefixar beskrivningen med ärende/titel
    om beskrivningen är för generisk för att stå på egna ben."""
    from analyzer import _expand_abbreviations
    raw = _load_dates_raw()
    cutoff = (date.today() - timedelta(days=7)).isoformat()

    # Ord som avslöjar att beskrivningen är generisk (kräver kontext)
    GENERIC_MARKERS = (
        "propositionen", "ärendet", "utskottet", "skrivelsen",
        "konsultationen", "dokumentet", "förslaget", "regeringen",
        "mötet", "mötet.", "beslutet",
    )

    def _add_context(beskr: str, title: str, arende: str) -> str:
        if not beskr:
            return beskr
        b_low = beskr.lower()
        # Om beskrivningen redan innehåller tydlig kontext, lämna som den är
        has_context = any(
            kw.lower() in b_low
            for kw in (title, arende)
            if kw and len(kw) > 8
        )
        if has_context:
            return beskr
        # Prefixa med ärende om finns, annars full titel (ingen truncering)
        context = arende or title
        if not context:
            return beskr
        return f"{context}: {beskr}"

    result = {}
    for k, entries in sorted(raw.items()):
        if k < cutoff:
            continue
        expanded = []
        for e in entries:
            new_e = dict(e)
            beskr = new_e.get("beskrivning") or ""
            title = new_e.get("title") or ""
            arende = new_e.get("arende") or ""
            if isinstance(beskr, str):
                # Steg 1: prefixa med ärende/titel om beskrivningen är generisk
                b_low = beskr.lower()
                if any(m in b_low for m in GENERIC_MARKERS):
                    beskr = _add_context(beskr, title, arende)
                # Steg 2: expandera förkortningar
                new_e["beskrivning"] = _expand_abbreviations(beskr)
            expanded.append(new_e)
        result[k] = expanded
    return result


def _load_dates_raw() -> dict:
    if not os.path.exists(DATES_FILE):
        return {}
    try:
        with open(DATES_FILE, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def get_yesterday() -> list[dict]:
    """Hämtar gårdagens ärenden."""
    yesterday = (date.today() - timedelta(days=1)).isoformat()
    return _load_raw().get(yesterday, [])


def get_last_week() -> list[dict]:
    """Hämtar ärenden från de senaste 7 dagarna (exkl. idag)."""
    raw = _load_raw()
    today_str = date.today().isoformat()
    cutoff = (date.today() - timedelta(days=7)).isoformat()
    result = []
    for day_str, items in raw.items():
        if cutoff <= day_str < today_str:
            result.extend(items)
    return result


def _load_raw() -> dict:
    if not os.path.exists(MEMORY_FILE):
        return {}
    try:
        with open(MEMORY_FILE, encoding="utf-8") as f:
            data = json.load(f)
        # Normalisera gamla URL:er med dubblat ID
        for day_items in data.values():
            for item in day_items:
                if item.get("url"):
                    item["url"] = _fix_url(item["url"])
        return data
    except Exception:
        return {}
