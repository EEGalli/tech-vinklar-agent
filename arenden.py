"""
Hanterar pågående lagstiftningsärenden — identifierade automatiskt av AI:n.
Sparar en persistent JSON-fil med alla kända ärenden och deras tidslinje.
"""
import json
import os
from datetime import date

ARENDEN_FILE = os.path.join(os.path.dirname(__file__), ".agent_arenden.json")


def load() -> dict:
    """Laddar alla sparade ärenden."""
    if not os.path.exists(ARENDEN_FILE):
        return {}
    try:
        with open(ARENDEN_FILE, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def save(arenden: dict) -> None:
    """Sparar ärenden till disk."""
    with open(ARENDEN_FILE, "w", encoding="utf-8") as f:
        json.dump(arenden, f, ensure_ascii=False, indent=2)


def update(arende_name: str, item: dict, next_step: str = "") -> None:
    """
    Lägger till ett dokument i ett ärende. Skapar ärendet om det inte finns.
    """
    arenden = load()

    today = date.today().isoformat()
    doc_entry = {
        "title": item.get("title", ""),
        "date": item.get("date", today)[:10],
        "url": item.get("url", ""),
        "source": item.get("source", ""),
        "tech_vinkel": item.get("analysis", {}).get("tech_vinkel", ""),
    }

    if arende_name not in arenden:
        # Nytt ärende
        arenden[arende_name] = {
            "name": arende_name,
            "first_seen": today,
            "last_updated": today,
            "status": "aktiv",
            "is_new_today": True,
            "is_updated_today": False,
            "next_step": next_step,
            "documents": [doc_entry],
        }
    else:
        existing = arenden[arende_name]
        existing["last_updated"] = today
        existing["is_new_today"] = False
        existing["is_updated_today"] = True
        if next_step:
            existing["next_step"] = next_step

        # Lägg bara till om dokumentet inte redan finns (deduplicera på titel)
        existing_titles = {d["title"] for d in existing["documents"]}
        if doc_entry["title"] not in existing_titles:
            existing["documents"].append(doc_entry)

        # Sortera tidslinje kronologiskt
        existing["documents"].sort(key=lambda d: d.get("date", ""))

    save(arenden)


def get_all() -> dict:
    """Returnerar alla ärenden, sorterade efter senast uppdaterade."""
    arenden = load()
    return dict(
        sorted(arenden.items(), key=lambda x: x[1].get("last_updated", ""), reverse=True)
    )
