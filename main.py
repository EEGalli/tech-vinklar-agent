#!/usr/bin/env python3
"""
Tech Vinklar Agent
==================
Automatisk bevakning av tech-relaterade politiska ärenden i EU och Riksdagen.

Användning:
  python main.py                    # Kör full analys, sparar digest
  python main.py --riksdagen-only   # Bara Riksdagen
  python main.py --eu-only          # Bara EU
  python main.py --no-ai            # Ingen Claude-analys, bara rådata
  python main.py --output digest.md # Ange utdatafil
"""
import argparse
import os
import sys
from datetime import datetime

# Lägg till projektkatalogen i Python-sökvägen
sys.path.insert(0, os.path.dirname(__file__))

import requests
import subprocess
from sources import riksdagen, europarl, eurlex, regeringen, enisa
from analyzer import analyze_batch, create_digest
from output.html_report import generate as generate_html
import memory as mem
import arenden as ar


def main():
    parser = argparse.ArgumentParser(description="Tech Vinklar Agent — EU & Riksdagen")
    parser.add_argument("--riksdagen-only", action="store_true", help="Bara Riksdagen")
    parser.add_argument("--eu-only", action="store_true", help="Bara EU-källorna")
    parser.add_argument("--no-ai", action="store_true", help="Ingen Claude-analys")
    parser.add_argument("--min-relevance", choices=["hög", "medel", "låg"], default="medel",
                        help="Minsta relevans att inkludera (default: medel)")
    parser.add_argument("--model", default="gemma3:12b",
                        help="Ollama-modell att använda (default: gemma3:12b)")
    parser.add_argument("--output", default=None, help="Utdatafil (default: auto-namnges)")
    parser.add_argument("--print", action="store_true", help="Skriv ut digest i terminalen")
    args = parser.parse_args()

    print("=" * 60)
    print("  Tech Vinklar Agent")
    print(f"  {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print("=" * 60)

    all_items = []

    # --- Parallell källhämtning — alla fyra källor är oberoende HTTP-anrop ---
    from concurrent.futures import ThreadPoolExecutor, as_completed
    fetch_tasks = []
    if not args.eu_only:
        fetch_tasks.append(("Riksdagen", riksdagen.fetch_all))
        fetch_tasks.append(("Regeringen.se", regeringen.fetch_all))
    if not args.riksdagen_only:
        fetch_tasks.append(("EU-parlamentet", europarl.fetch_all))
        fetch_tasks.append(("EUR-Lex", eurlex.fetch_all))
        fetch_tasks.append(("ENISA", enisa.fetch_all))

    print(f"\nHämtar från {len(fetch_tasks)} källor parallellt...")
    with ThreadPoolExecutor(max_workers=len(fetch_tasks)) as pool:
        future_to_name = {pool.submit(fn): name for name, fn in fetch_tasks}
        for future in as_completed(future_to_name):
            name = future_to_name[future]
            try:
                items = future.result()
                print(f"  ✓ {name}: {len(items)} tech-relevanta")
                all_items.extend(items)
            except Exception as e:
                print(f"  ✗ {name}: {e}")

    # Filtrera bort ärenden vars URL är en ren startsida (t.ex. riksdagen.se/ eller europarl.europa.eu/)
    def _is_homepage_url(item: dict) -> bool:
        url = item.get("url", "")
        if not url:
            return False  # Tom URL är OK — dokumentet kanske inte är publicerat än
        from urllib.parse import urlparse
        path = urlparse(url).path.strip("/")
        return not bool(path)  # True om URL saknar sökväg = startsida

    before = len(all_items)
    all_items = [i for i in all_items if not _is_homepage_url(i)]
    dropped = before - len(all_items)
    if dropped:
        print(f"  ({dropped} ärenden med startsida som käll-URL filtrerades bort)")

    # Deduplicera identiska titlar (case-insensitive, strippa whitespace)
    def _norm_title(t: str) -> str:
        return " ".join(t.lower().split())

    seen_titles: set[str] = set()
    deduped = []
    for item in all_items:
        key = _norm_title(item.get("title", ""))
        if key and key not in seen_titles:
            seen_titles.add(key)
            deduped.append(item)
    dup_count = len(all_items) - len(deduped)
    if dup_count:
        print(f"  ({dup_count} dublett-titlar slogs ihop)")
    all_items = deduped

    print(f"\nTotalt: {len(all_items)} ärenden att analysera")

    if not all_items:
        print("Inga ärenden hittades. Kontrollera nätverksanslutning och API-status.")
        return

    # --- Claude-analys ---
    if not args.no_ai:
        # Kontrollera att Ollama svarar
        # Hoppa över Ollama-pingen om Gemini API-nyckel finns (då kör vi mot Gemini)
        from analyzer import _use_gemini as _check_gemini
        if not _check_gemini():
            try:
                r = requests.get("http://localhost:11434/api/tags", timeout=5)
                r.raise_for_status()
            except Exception:
                print("\nVARNING: Ollama svarar inte på http://localhost:11434.")
                print("Starta Ollama och försök igen, eller kör med --no-ai.")
                args.no_ai = True

    if not args.no_ai:
        from analyzer import _use_gemini, GEMINI_MODEL
        backend = f"Gemini ({GEMINI_MODEL})" if _use_gemini() else f"Ollama ({args.model})"
        print(f"\nAnalyserar med {backend} — min relevans: {args.min_relevance}...")
        analyzed = analyze_batch(all_items, min_relevance=args.min_relevance, model=args.model)
        print(f"  {len(analyzed)} ärenden passerade relevansfiltret")

        # Klustra per ärende: om flera dokument delar arende-namn, behåll bara
        # det mest relevanta i huvudlistan. De övriga lever vidare i ärende-tidslinjen.
        relevance_order = {"hög": 0, "medel": 1, "låg": 2, "okänd": 3}
        best_per_arende: dict[str, dict] = {}
        standalone: list[dict] = []
        for item in analyzed:
            arende = (item.get("analysis", {}).get("arende") or "").strip()
            if arende and arende.lower() != "null":
                current = best_per_arende.get(arende)
                if current is None:
                    best_per_arende[arende] = item
                else:
                    new_level = relevance_order.get(item.get("analysis", {}).get("relevans", "okänd"), 3)
                    cur_level = relevance_order.get(current.get("analysis", {}).get("relevans", "okänd"), 3)
                    if new_level < cur_level:
                        best_per_arende[arende] = item
            else:
                standalone.append(item)
        clustered = list(best_per_arende.values()) + standalone
        collapsed = len(analyzed) - len(clustered)
        if collapsed:
            print(f"  {collapsed} dokument klustrades in under befintliga ärenden")
        analyzed = clustered
    else:
        # Utan AI: inkludera alla med placeholder-analys
        for item in all_items:
            item["analysis"] = {
                "relevans": "okänd",
                "tech_vinkel": "AI-analys ej aktiverad",
                "varfor_viktigt": "",
                "eu_koppling": None,
                "keywords": [],
            }
        analyzed = all_items

    # --- Spara till minnet (för summering imorgon) ---
    mem.save(analyzed)
    mem.save_dates(analyzed)

    # --- Generera HTML-rapport (sparas i reports/YYYY-MM/) ---
    now_dt = datetime.now()
    date_slug = now_dt.strftime("%Y%m%d_%H%M")
    if args.output:
        html_file = args.output
    else:
        month_dir = os.path.join("reports", now_dt.strftime("%Y-%m"))
        os.makedirs(month_dir, exist_ok=True)
        html_file = os.path.join(month_dir, f"digest_{date_slug}.html")
    generate_html(
        analyzed,
        output_path=html_file,
        yesterday=mem.get_yesterday(),
        last_week=mem.get_last_week(),
        arenden=ar.get_all(),
        important_dates=mem.get_important_dates(),
    )
    print(f"\nRapport sparad: {html_file}")

    # Öppna automatiskt i webbläsaren
    try:
        subprocess.run(["open", html_file])
    except Exception:
        pass

    print("Klart!")


if __name__ == "__main__":
    main()
