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
import time
from concurrent.futures import TimeoutError as FuturesTimeoutError
from datetime import datetime

# Lägg till projektkatalogen i Python-sökvägen
sys.path.insert(0, os.path.dirname(__file__))

import requests
import subprocess
from sources import (riksdagen, eurlex, regeringen, enisa,
                     eu_agencies, tech_news, svenska_myndigheter)
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
        fetch_tasks.append(("Svenska myndigheter (IMY+MSB)", svenska_myndigheter.fetch_all))
    if not args.riksdagen_only:
        fetch_tasks.append(("EUR-Lex", eurlex.fetch_all))
        fetch_tasks.append(("ENISA", enisa.fetch_all))
        fetch_tasks.append(("EU-byråer", eu_agencies.fetch_all))
        fetch_tasks.append(("Tech-policy-media", tech_news.fetch_all))

    print(f"\nHämtar från {len(fetch_tasks)} källor parallellt...")
    # Global tidsgräns (5 min): en enskild långsam källa får inte blockera allt.
    # Per-källa-timeout: 3 min — räcker för Riksdagens långsamma sidor.
    PER_SOURCE_TIMEOUT = 180  # 3 minuter
    OVERALL_TIMEOUT = 300     # 5 minuter
    overall_start = time.monotonic()
    with ThreadPoolExecutor(max_workers=len(fetch_tasks)) as pool:
        future_to_name = {pool.submit(fn): name for name, fn in fetch_tasks}
        for future in as_completed(future_to_name, timeout=OVERALL_TIMEOUT):
            name = future_to_name[future]
            elapsed = time.monotonic() - overall_start
            remaining = max(1, OVERALL_TIMEOUT - elapsed)
            try:
                items = future.result(timeout=min(PER_SOURCE_TIMEOUT, remaining))
                print(f"  ✓ {name}: {len(items)} tech-relevanta")
                all_items.extend(items)
            except FuturesTimeoutError:
                print(f"  ⏱ {name}: tog för lång tid (>{PER_SOURCE_TIMEOUT}s) — hoppar över")
            except Exception as e:
                print(f"  ✗ {name}: {type(e).__name__}: {e}")
        # Avbryt eventuellt fortfarande pågående futures så pool stängs snabbt
        for f in future_to_name:
            if not f.done():
                f.cancel()

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

    # --- AI-analys: diagnostik först så vi vet vilken backend som används ---
    if not args.no_ai:
        from analyzer import _use_gemini as _check_gemini, _use_groq as _check_groq
        # Diagnostik: vilka nycklar är satta, vilka filer finns?
        env_files_found = [
            f for f in (".env", ".env.local")
            if os.path.exists(os.path.join(os.path.dirname(__file__), f))
        ]
        if env_files_found:
            print(f"  AI-config: läser från {env_files_found}", flush=True)
        else:
            paused = [
                f for f in os.listdir(os.path.dirname(__file__) or ".")
                if f.startswith(".env.") and f != ".env"
            ]
            if paused:
                print(
                    f"  AI-config: ingen .env hittad, men dessa pausade filer finns: {paused}. "
                    f"Döp om till .env för att aktivera",
                    flush=True,
                )
        has_groq = _check_groq()
        has_gemini = _check_gemini()
        print(f"  AI-nycklar: Groq={'✓' if has_groq else '✗'}, Gemini={'✓' if has_gemini else '✗'}", flush=True)

        # Om varken Groq eller Gemini finns måste Ollama svara, annars hoppa AI helt
        if not (has_groq or has_gemini):
            try:
                r = requests.get("http://localhost:11434/api/tags", timeout=5)
                r.raise_for_status()
                print(f"  AI-backend: Ollama (lokal) — varken cloud-AI eller Gemini tillgängliga", flush=True)
            except Exception:
                print("\nVARNING: Ingen AI-backend tillgänglig.")
                print("  - Sätt GROQ_API_KEY eller GEMINI_API_KEY i .env, eller")
                print("  - Starta Ollama på http://localhost:11434, eller")
                print("  - Kör med --no-ai för bara rådata.")
                args.no_ai = True

    if not args.no_ai:
        from analyzer import _use_gemini, _use_groq, GEMINI_MODEL, GROQ_MODEL
        if _use_groq():
            backend = f"Groq ({GROQ_MODEL})"
        elif _use_gemini():
            backend = f"Gemini ({GEMINI_MODEL})"
        else:
            backend = f"Ollama ({args.model})"
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

    # Auto-pusha till GitHub så Streamlit-appen alltid visar senaste rapporten
    _auto_push(html_file)

    print("Klart!")


def _auto_push(html_file: str) -> None:
    """Stagar reports/ + state-filer och försöker pusha till GitHub.
    Failar tyst om: inte ett git-repo, inget att committa, eller offline."""
    project_dir = os.path.dirname(os.path.abspath(__file__))
    if not os.path.exists(os.path.join(project_dir, ".git")):
        return  # Inte ett git-repo

    try:
        # Stagea bara det vi vill ha med
        subprocess.run(
            ["git", "add", "reports/", ".agent_memory.json", ".agent_arenden.json",
             ".agent_dates.json", ".agent_analysis_cache.json"],
            cwd=project_dir, check=False, capture_output=True,
        )
        # Något att committa?
        diff = subprocess.run(
            ["git", "diff", "--staged", "--quiet"],
            cwd=project_dir, capture_output=True,
        )
        if diff.returncode == 0:
            print("(inget att pusha — inga nya filer sedan senaste commit)")
            return

        commit_msg = f"Lokal körning {datetime.now().strftime('%Y-%m-%d %H:%M')}"
        commit = subprocess.run(
            ["git", "commit", "-m", commit_msg],
            cwd=project_dir, capture_output=True, text=True,
        )
        if commit.returncode != 0:
            print(f"(commit failade: {commit.stderr.strip()[:120]})")
            return

        # Pulla först ifall Action har pushat. Använd "ours"-strategi —
        # state-filerna (memory/cache/arenden) är resultatet av senaste körning,
        # vår lokala version är alltid den korrekta efter en lokal main.py-körning.
        pull = subprocess.run(
            ["git", "pull", "--rebase", "--strategy-option=ours"],
            cwd=project_dir, capture_output=True, text=True, timeout=60,
        )
        if pull.returncode != 0:
            # Försök automatiskt resolva merge-konflikter på state-filer
            print(f"(pull-konflikt — försöker resolva med 'ours'-strategi)")
            subprocess.run(
                ["git", "checkout", "--ours", ".agent_memory.json",
                 ".agent_arenden.json", ".agent_dates.json",
                 ".agent_analysis_cache.json"],
                cwd=project_dir, capture_output=True,
            )
            subprocess.run(
                ["git", "add", ".agent_memory.json", ".agent_arenden.json",
                 ".agent_dates.json", ".agent_analysis_cache.json"],
                cwd=project_dir, capture_output=True,
            )
            env = {**os.environ, "GIT_EDITOR": "true"}
            rebase_cont = subprocess.run(
                ["git", "rebase", "--continue"],
                cwd=project_dir, capture_output=True, text=True, env=env, timeout=30,
            )
            # Säkerhetsnät: om rebase-continue också failade, abort:a hellre
            # än att lämna repot i halvrebasat tillstånd som nästa körning kraschar på
            if rebase_cont.returncode != 0:
                print(f"(rebase --continue failade: {rebase_cont.stderr.strip()[:150]})")
                print("(kör 'git rebase --abort' som säkerhetsnät — lämnar repo i clean tillstånd)")
                subprocess.run(
                    ["git", "rebase", "--abort"],
                    cwd=project_dir, capture_output=True, timeout=15,
                )
                return  # Pusha inte — repo är i originalstate, säkrare

        push = subprocess.run(
            ["git", "push"],
            cwd=project_dir, capture_output=True, text=True, timeout=30,
        )
        if push.returncode == 0:
            print("✓ Pushat till GitHub — Streamlit-appen uppdateras inom 1 min.")
        else:
            # Spara felet i en fil så det inte tystas — Streamlit kan visa varning
            err_msg = push.stderr.strip()[:500]
            print(f"(push failade: {err_msg[:200]})")
            try:
                err_file = os.path.join(project_dir, ".last_push_error")
                with open(err_file, "w") as f:
                    f.write(f"{datetime.now().isoformat()}\n{err_msg}\n")
            except Exception:
                pass
    except Exception as e:
        print(f"(auto-push kunde inte köras: {type(e).__name__}: {e})")


if __name__ == "__main__":
    main()
