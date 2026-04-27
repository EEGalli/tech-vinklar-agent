#!/usr/bin/env python3
"""
Städar .agent_arenden.json:
1. Raderar ärenden vars namn är ett ämne (inte en specifik lag/proposition)
2. Raderar ärenden som handlar om icke-tech-frågor
3. Avdubblar dokument som ligger under flera ärenden (behåller dem där de
   bäst passar baserat på titel-matchning mot ärende-namnet)
"""
import json
import os

ARENDEN_FILE = os.path.join(os.path.dirname(__file__), ".agent_arenden.json")

# Ärende-namn att radera (ämnen, inte ärenden)
TOPICS_TO_REMOVE = {
    "AI", "Cybersäkerhet", "Plattformsreglering", "Halvledare",
    "Dataskydd och integritet", "Digital infrastruktur",
    "Uppkopplade fordon", "Sociala medier", "Övrigt tech",
    "Rymd- och försvarsstrategi",  # Ämne, ej ärende
}

# Ärende-namn att radera (icke-tech)
NON_TECH_TO_REMOVE = {
    "Hyresgarantier",
    "Krigsmaterielsexport",
    "Vindkraftsutbyggnad",
    "Stärkt kontroll av livsmedelskedjan",
    "Stärkt lagstiftning mot hedersrelaterat våld",
}

# Dokument som ligger under FEL ärende — ta bort dem från specifika ärenden
# (nyckel = arende-namn, värde = lista titlar som ska tas bort därifrån)
WRONG_ASSIGNMENTS = {
    "Cybersäkerhetscenter": [
        "Uppskov med behandlingen av vissa ärenden",
        "Svenskt bidrag till Natos framskjutna närvaro i Finland",
    ],
    "Elfordonsstrategi": [
        "Extra ändringsbudget för 2026 – Sänkt skatt på drivmedel",
        "Extra ändringsbudget för 2026 – sänkt skatt på drivmedel",
        "Nya lagar om elsystemet",  # hör INTE hit
    ],
}


def _title_matches(doc_title: str, patterns: list[str]) -> bool:
    t = doc_title.lower()
    return any(p.lower() in t for p in patterns)


def clean():
    if not os.path.exists(ARENDEN_FILE):
        print("Ingen arenden-fil hittad — inget att städa")
        return

    with open(ARENDEN_FILE, encoding="utf-8") as f:
        arenden = json.load(f)

    before = len(arenden)

    # Steg 1: radera ämnen och icke-tech
    to_delete = []
    for name in arenden:
        if name in TOPICS_TO_REMOVE:
            to_delete.append(("ämne", name))
        elif name in NON_TECH_TO_REMOVE:
            to_delete.append(("icke-tech", name))
    for reason, name in to_delete:
        del arenden[name]
        print(f"  ✗ Raderad ({reason}): {name}")

    # Steg 2: rensa fel-tilldelade dokument inom befintliga ärenden
    for arende_name, wrong_titles in WRONG_ASSIGNMENTS.items():
        if arende_name not in arenden:
            continue
        entry = arenden[arende_name]
        docs = entry.get("documents", [])
        kept = [d for d in docs if not _title_matches(d.get("title", ""), wrong_titles)]
        removed = len(docs) - len(kept)
        if removed:
            entry["documents"] = kept
            print(f"  ⟳ {arende_name}: tog bort {removed} felklassade dokument")

    after = len(arenden)
    with open(ARENDEN_FILE, "w", encoding="utf-8") as f:
        json.dump(arenden, f, ensure_ascii=False, indent=2)

    print(f"\n{before} → {after} ärenden ({before - after} raderade)")


if __name__ == "__main__":
    clean()
