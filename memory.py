"""
Enkelt minne — sparar varje körnings ärenden till en JSON-fil
så att nästa körning kan summera vad som hände igår / i veckan.
"""
import json
import os
import re
import time
from datetime import datetime, date, timedelta


def _atomic_write(path: str, data: dict) -> None:
    """Skriv JSON atomärt: tmp-fil + rename. Förhindrar halvskrivna filer
    vid avbruten process (CI-timeout, Ctrl-C)."""
    tmp = f"{path}.tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def _backup_corrupt(path: str, reason: str) -> None:
    """Döper om en korrupt fil till .corrupt-<ts> så data inte tyst skrivs över."""
    backup = f"{path}.corrupt-{int(time.time())}"
    try:
        os.replace(path, backup)
        print(f"  ⚠ KORRUPT {os.path.basename(path)}: {reason} — backup: {backup}", flush=True)
    except OSError as e:
        print(f"  ⚠ KORRUPT {os.path.basename(path)}: {reason} (backup misslyckades: {e})", flush=True)


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
            "committee": i.get("committee", ""),  # för regeringen: vilka departement
            "summary": i.get("summary", ""),
            "analysis": i.get("analysis", {}),
        }
        for i in items
    ]
    # Behåll bara senaste 30 dagarna
    cutoff = (date.today() - timedelta(days=30)).isoformat()
    trimmed = {k: v for k, v in existing.items() if k >= cutoff}
    _atomic_write(MEMORY_FILE, trimmed)


def save_analysis_cache(items: list[dict], max_age_days: int = 30) -> None:
    """Sparar ALLA analyserade items (inkl filtrerade icke-tech) till en cache.
    Nästa körning använder detta för att undvika att re-analysera samma URL:er.
    Keyed på URL med timestamp — items äldre än max_age_days rensas automatiskt."""
    print(f"  [save_analysis_cache] anropad med {len(items)} items, fil={CACHE_FILE}", flush=True)
    cache = load_analysis_cache()
    print(f"  [save_analysis_cache] befintlig cache hade {len(cache)} items", flush=True)
    today = date.today().isoformat()
    skipped_no_url = 0
    skipped_no_analysis = 0
    skipped_error = 0
    skipped_unknown = 0
    added = 0
    for item in items:
        url = item.get("url") or ""
        analysis = item.get("analysis")
        if not url:
            skipped_no_url += 1
            continue
        if not analysis:
            skipped_no_analysis += 1
            continue
        tech_vinkel = analysis.get("tech_vinkel") or ""
        if tech_vinkel.startswith("Fel:"):
            skipped_error += 1
            continue
        if analysis.get("relevans") == "okänd":
            skipped_unknown += 1
            continue
        cache[url] = {
            "analysis": analysis,
            "title": item.get("title", ""),
            "source": item.get("source", ""),
            "type": item.get("type", ""),
            "date": item.get("date", ""),
            "committee": item.get("committee", ""),
            "summary": item.get("summary", ""),
            "cached_at": today,
        }
        added += 1
    print(
        f"  [save_analysis_cache] {added} tillagda | skippade: "
        f"no_url={skipped_no_url} no_analysis={skipped_no_analysis} "
        f"error={skipped_error} unknown={skipped_unknown}",
        flush=True,
    )
    # Rensa poster äldre än max_age_days
    cutoff = (date.today() - timedelta(days=max_age_days)).isoformat()
    cache = {u: e for u, e in cache.items() if e.get("cached_at", "") >= cutoff}
    _atomic_write(CACHE_FILE, cache)
    print(f"  [save_analysis_cache] skrev {len(cache)} items till disk", flush=True)


def load_analysis_cache() -> dict:
    """Läser analys-cachen. Returnerar {url: {analysis, title, cached_at}}.
    Säkerhetskopierar korrupt fil innan tom dict returneras."""
    if not os.path.exists(CACHE_FILE):
        return {}
    try:
        with open(CACHE_FILE, encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        _backup_corrupt(CACHE_FILE, str(e))
        return {}


def save_dates(items: list[dict]) -> None:
    """Sparar viktiga datum från AI-analysen till en separat fil.
    Dedupe på (datum + URL): primärnyckel är URL. Om samma titel kommer från
    OLIKA URL:er (t.ex. Riksdagen + regeringen rapporterar samma sak) → KOMBINERA
    källorna i en "urls"-lista, så att en samlad post visas istället för dubbletter."""
    existing = _load_dates_raw()
    for item in items:
        item_title = item.get("title", "")
        item_url = item.get("url", "")
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
                "url": item_url,
                "arende": item.get("analysis", {}).get("arende", ""),
            }
            # Primär: samma URL = uppdatera befintlig
            match_url = next(
                (e for e in existing[datum] if e.get("url") == item_url and item_url),
                None,
            )
            if match_url is not None:
                match_url.update(entry)
                continue
            # Sekundär: samma titel, OLIKA URL → KOMBINERA källor
            match_title = next(
                (e for e in existing[datum] if e.get("title") == item_title and item_title),
                None,
            )
            if match_title is not None:
                existing_urls = match_title.get("urls") or [match_title.get("url", "")]
                if item_url and item_url not in existing_urls:
                    existing_urls.append(item_url)
                match_title["urls"] = [u for u in existing_urls if u]
                # Behåll längsta beskrivning (mest info)
                if len(beskrivning) > len(match_title.get("beskrivning", "")):
                    match_title["beskrivning"] = beskrivning
                continue
            existing[datum].append(entry)
    _atomic_write(DATES_FILE, existing)


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
        "debatt", "riksdagen", "omröstning", "vote ",
        "behandlar", "behandlas", "behandling", "lämnas",
        "publiceras", "presenteras",
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
    except Exception as e:
        _backup_corrupt(DATES_FILE, str(e))
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
    except Exception as e:
        _backup_corrupt(MEMORY_FILE, str(e))
        return {}
