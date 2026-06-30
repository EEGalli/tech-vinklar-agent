"""
Hanterar pågående lagstiftningsärenden — identifierade automatiskt av AI:n.
Sparar en persistent JSON-fil med alla kända ärenden och deras tidslinje.
"""
import json
import os
import time
from datetime import date, datetime
from email.utils import parsedate_to_datetime

ARENDEN_FILE = os.path.join(os.path.dirname(__file__), ".agent_arenden.json")


def _to_iso_date(raw: str, fallback: str) -> str:
    """Normaliserar datum till ISO YYYY-MM-DD oavsett inkommande format.
    Hanterar både ISO och RFC 822 (RSS pubDate). Faller tillbaka vid behov."""
    if not raw:
        return fallback
    s = raw.strip()
    # Redan ISO?
    if len(s) >= 10 and s[4] == "-" and s[7] == "-":
        try:
            return datetime.fromisoformat(s[:19].replace("Z", "")).date().isoformat()
        except ValueError:
            pass
        # Sista chansen: bara första 10 tecken om de ser ISO-aktiga ut
        return s[:10]
    # RFC 822 (RSS pubDate)
    try:
        return parsedate_to_datetime(s).date().isoformat()
    except (TypeError, ValueError):
        pass
    return fallback


def load() -> dict:
    """Laddar alla sparade ärenden. Vid korrupt fil: säkerhetskopia + logga
    så att data inte tyst skrivs över med tom struktur vid nästa save."""
    if not os.path.exists(ARENDEN_FILE):
        return {}
    try:
        with open(ARENDEN_FILE, encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        backup = f"{ARENDEN_FILE}.corrupt-{int(time.time())}"
        try:
            os.replace(ARENDEN_FILE, backup)
            print(
                f"  ⚠ KORRUPT ärende-fil ({ARENDEN_FILE}): {e} — "
                f"säkerhetskopierad till {backup}, börjar om från tom",
                flush=True,
            )
        except OSError as rename_err:
            print(
                f"  ⚠ KORRUPT ärende-fil ({ARENDEN_FILE}): {e} "
                f"(kunde inte säkerhetskopiera: {rename_err})",
                flush=True,
            )
        return {}


def save(arenden: dict) -> None:
    """Atomär save: skriv till .tmp + os.replace så att en avbruten skrivning
    aldrig lämnar filen halvskriven (vilket annars skulle tolkas som korrupt
    nästa körning och orsaka dataförlust)."""
    tmp = f"{ARENDEN_FILE}.tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(arenden, f, ensure_ascii=False, indent=2)
    os.replace(tmp, ARENDEN_FILE)


def update(arende_name: str, item: dict, next_step: str = "") -> None:
    """
    Lägger till ett dokument i ett ärende. Skapar ärendet om det inte finns.
    """
    arenden = load()

    today = date.today().isoformat()
    doc_entry = {
        "title": item.get("title", ""),
        "date": _to_iso_date(item.get("date", ""), today),
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

        # Dedupera primärt på URL (säker), fall tillbaka till titel om URL saknas
        existing_keys = {
            (d.get("url") or d.get("title", ""))
            for d in existing["documents"]
        }
        new_key = doc_entry.get("url") or doc_entry.get("title", "")
        if new_key and new_key not in existing_keys:
            existing["documents"].append(doc_entry)

        # Sortera tidslinje kronologiskt — datum är ISO så lexikografisk sort funkar
        existing["documents"].sort(key=lambda d: d.get("date", ""))

    save(arenden)


def get_all() -> dict:
    """Returnerar alla ärenden, sorterade efter senast uppdaterade."""
    arenden = load()
    return dict(
        sorted(arenden.items(), key=lambda x: x[1].get("last_updated", ""), reverse=True)
    )
