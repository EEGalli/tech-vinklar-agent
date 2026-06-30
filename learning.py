"""
Lärdomssystem — extraherar mönster från användarens manuella prioritet-ändringar.

När Evelina sätter en prioritet via ✏️-knappen i HTML-rapporten sparas det
i .agent_overrides.json som {url: relevans}. Den här modulen läser tillbaka
det + matchar mot memory (för att veta vilket ärende/källa/keywords varje URL
tillhörde) och bygger upp mönster som AI:n kan använda framöver.

Mönster vi extraherar:
- per ärende: "AI Act-items har hon satt till hög 4 gånger"
- per keyword: "CSAM-items markerade hög 3 av 3 gånger"
- per källa: "EDRi-items markerade medel 2 av 2"
- per committee: "Justitiedepartementet om biometri = hög"

Trösklar:
- ≥2 samma mönster → AI får hint i prompten
- ≥3 samma mönster med 100% enighet → säkerhetsnät overridar AI:s val
"""
import os
import json
from collections import Counter, defaultdict

OVERRIDES_FILE = os.path.join(os.path.dirname(__file__), ".agent_overrides.json")


def _load_overrides() -> dict[str, str]:
    if not os.path.exists(OVERRIDES_FILE):
        return {}
    try:
        with open(OVERRIDES_FILE, encoding="utf-8") as f:
            data = json.load(f)
        result = {}
        for url, val in data.items():
            if isinstance(val, str) and val in ("hög", "medel", "låg"):
                result[url] = val
            elif isinstance(val, dict) and val.get("relevans") in ("hög", "medel", "låg"):
                result[url] = val["relevans"]
        return result
    except Exception:
        return {}


def _load_memory_lookup() -> dict[str, dict]:
    """Returnerar {url: {arende, source, keywords, committee}} från memory."""
    try:
        import memory as mem
        raw = mem._load_raw()
    except Exception:
        return {}
    lookup = {}
    for items in raw.values():
        for it in items:
            url = it.get("url", "")
            if not url:
                continue
            analysis = it.get("analysis", {}) or {}
            lookup[url] = {
                "arende": (analysis.get("arende") or "").strip().lower(),
                "source": it.get("source", ""),
                "committee": it.get("committee", ""),
                "keywords": [k.lower() for k in (analysis.get("keywords") or [])],
                "title": it.get("title", ""),
            }
    return lookup


def extract_patterns() -> dict:
    """Bygger upp mönster: {dimension: {value: Counter{rel: n}}}.
    Dimensioner: arende, keyword, source, committee.
    Returnerar bara mönster där användaren satt ≥2 items."""
    overrides = _load_overrides()
    if not overrides:
        return {"arende": {}, "keyword": {}, "source": {}, "committee": {}}

    lookup = _load_memory_lookup()
    patterns = {
        "arende": defaultdict(Counter),
        "keyword": defaultdict(Counter),
        "source": defaultdict(Counter),
        "committee": defaultdict(Counter),
    }

    for url, rel in overrides.items():
        meta = lookup.get(url, {})
        arende = meta.get("arende") or ""
        if arende:
            patterns["arende"][arende][rel] += 1
        for kw in meta.get("keywords", []):
            patterns["keyword"][kw][rel] += 1
        src = meta.get("source") or ""
        if src:
            patterns["source"][src][rel] += 1
        committee = meta.get("committee") or ""
        if committee:
            patterns["committee"][committee][rel] += 1

    # Filtrera bort mönster med <2 datapunkter (inte tillräckligt med signal)
    result = {}
    for dim, by_val in patterns.items():
        result[dim] = {}
        for val, counter in by_val.items():
            total = sum(counter.values())
            if total >= 2:
                result[dim][val] = dict(counter)
    return result


def build_prompt_hint(patterns: dict, max_per_dim: int = 5) -> str:
    """Bygger en kompakt text till AI-prompten med användarens preferenser.
    Returnerar tom sträng om inga mönster finns."""
    lines = []

    for dim_label, dim_key in [
        ("ärende", "arende"),
        ("källa", "source"),
        ("nyckelord", "keyword"),
    ]:
        by_val = patterns.get(dim_key, {})
        if not by_val:
            continue
        # Sortera på majoritet × antal (starkaste signal först)
        scored = []
        for val, counter in by_val.items():
            total = sum(counter.values())
            top_rel, top_n = max(counter.items(), key=lambda x: x[1])
            confidence = top_n / total
            scored.append((val, top_rel, top_n, total, confidence))
        scored.sort(key=lambda x: (x[4], x[3]), reverse=True)
        scored = scored[:max_per_dim]
        for val, top_rel, top_n, total, conf in scored:
            agreement = f"{top_n}/{total}"
            lines.append(f"  - {dim_label} '{val}' → {top_rel} ({agreement} gånger)")

    if not lines:
        return ""

    return (
        "\n\nANVÄNDARENS PREFERENSER (baserat på tidigare manuella prioritet-val):\n"
        "Använd dessa som STARK INDIKATION när du sätter relevans:\n"
        + "\n".join(lines)
    )


def suggest_relevans(
    item_arende: str,
    item_keywords: list[str],
    item_source: str,
    item_committee: str,
    patterns: dict = None,
    min_confidence: float = 1.0,
    min_count: int = 3,
) -> tuple[str | None, str]:
    """Säkerhetsnät: returnerar (relevans, motivering) om mönstren är så starka att
    AI:n bör override:as. Annars (None, "").
    Default: kräver 3+ tidigare val med 100% enighet."""
    if patterns is None:
        patterns = extract_patterns()

    candidates = []  # (rel, count, dimension_label, value)

    arende_low = (item_arende or "").lower()
    if arende_low and arende_low in patterns.get("arende", {}):
        counter = patterns["arende"][arende_low]
        total = sum(counter.values())
        top_rel, top_n = max(counter.items(), key=lambda x: x[1])
        if top_n >= min_count and top_n / total >= min_confidence:
            candidates.append((top_rel, top_n, "ärende", item_arende))

    for kw in (item_keywords or []):
        kw_low = kw.lower()
        if kw_low in patterns.get("keyword", {}):
            counter = patterns["keyword"][kw_low]
            total = sum(counter.values())
            top_rel, top_n = max(counter.items(), key=lambda x: x[1])
            if top_n >= min_count and top_n / total >= min_confidence:
                candidates.append((top_rel, top_n, "nyckelord", kw))

    if item_source and item_source in patterns.get("source", {}):
        counter = patterns["source"][item_source]
        total = sum(counter.values())
        top_rel, top_n = max(counter.items(), key=lambda x: x[1])
        if top_n >= min_count and top_n / total >= min_confidence:
            candidates.append((top_rel, top_n, "källa", item_source))

    if not candidates:
        return None, ""

    # Välj den starkaste signalen (flest tidigare val)
    candidates.sort(key=lambda x: x[1], reverse=True)
    rel, n, dim_label, val = candidates[0]
    return rel, f"Användaren har satt {dim_label} '{val}' till {rel} {n} gånger tidigare"
