#!/usr/bin/env python3
"""
Sync-check: verifierar att retroaktiv logik och AI-prompter är i synk.

Kör före varje push:  python3 check_ai_prompt_sync.py

Exit code 0 = allt synkat. Exit code 1 = osync hittat, se felmeddelanden.

Kollar följande sync-punkter:
  1. TEMA_ORDER (html_report.py) matchar tema-listan i analyzer.py prompt
  2. _refine_tema-teman finns i TEMA_ORDER
  3. _should_exclude-patterns (streamlit_app.py) matchar analyzer.py EXCLUDE_PATTERNS
  4. _DOWNGRADE_PREFIXES i analyzer.py används konsekvent

Om du lägger till en ny sync-punkt: dokumentera i STRUCTURAL_INVARIANTS.md
och lägg till en check-funktion här.
"""
import re
import sys
from pathlib import Path

ROOT = Path(__file__).parent

FAILED: list[str] = []
OK: list[str] = []


def check(label: str, ok: bool, detail: str = "") -> None:
    if ok:
        OK.append(f"✓ {label}")
    else:
        FAILED.append(f"✗ {label}: {detail}")


def _read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def check_tema_order_in_prompt() -> None:
    """TEMA_ORDER från html_report.py måste finnas i analyzer.py-prompten som teman."""
    html_src = _read("output/html_report.py")
    ana_src = _read("analyzer.py")

    # Extrahera TEMA_ORDER
    m = re.search(r"TEMA_EMOJI\s*=\s*\{([^}]+)\}", html_src, re.DOTALL)
    if not m:
        check("TEMA_ORDER hittas", False, "kunde inte hitta TEMA_EMOJI i html_report.py")
        return
    tema_names = re.findall(r'"([^"]+)":\s*"[^"]*"', m.group(1))

    # Sök alla teman i analyzer.py-prompten (rad ~445)
    prompt_teman_match = re.search(
        r'"tema":\s*"[^"]*?Välj EXAKT ETT av:\s*([^"]+?)\. Om inget passar',
        ana_src,
    )
    if not prompt_teman_match:
        check("Prompt-teman hittas i analyzer.py", False,
              "kunde inte hitta 'tema' i prompt-mallen (leta efter rad ~445)")
        return
    prompt_text = prompt_teman_match.group(1)
    prompt_teman = re.findall(r"'([^']+)'", prompt_text)

    missing_in_prompt = [t for t in tema_names if t not in prompt_teman]
    extra_in_prompt = [t for t in prompt_teman if t not in tema_names]

    check(
        "TEMA_ORDER matchar prompt-teman",
        not missing_in_prompt and not extra_in_prompt,
        detail=(
            f"i html_report.py men saknas i prompt: {missing_in_prompt}; "
            f"i prompt men saknas i TEMA_ORDER: {extra_in_prompt}"
        ),
    )


def check_refine_tema_uses_valid_temas() -> None:
    """_TEMA_KEYWORD_RULES i html_report.py får bara peka på teman i TEMA_ORDER."""
    src = _read("output/html_report.py")
    m_order = re.search(r"TEMA_EMOJI\s*=\s*\{([^}]+)\}", src, re.DOTALL)
    if not m_order:
        return
    tema_names = set(re.findall(r'"([^"]+)":\s*"[^"]*"', m_order.group(1)))

    m_rules = re.search(r"_TEMA_KEYWORD_RULES\s*=\s*\[([\s\S]+?)^\]", src, re.MULTILINE)
    if not m_rules:
        check("_TEMA_KEYWORD_RULES hittas", False, "kunde inte hitta rules-listan")
        return
    # Varje regel: ("Temanamn", (kw1, kw2, ...)) — matcha bara första strängen efter (
    used_temas = re.findall(r'^\s*\(\s*"([^"]+)",\s*\(', m_rules.group(1), re.MULTILINE)

    invalid = [t for t in used_temas if t not in tema_names]
    check(
        "_TEMA_KEYWORD_RULES pekar bara på giltiga teman",
        not invalid,
        detail=f"okända teman i rules: {invalid}",
    )


def check_legacy_tema_map_targets_valid() -> None:
    """_LEGACY_TEMA_MAP måste peka på existerande teman (eller None)."""
    src = _read("output/html_report.py")
    m_order = re.search(r"TEMA_EMOJI\s*=\s*\{([^}]+)\}", src, re.DOTALL)
    if not m_order:
        return
    tema_names = set(re.findall(r'"([^"]+)":\s*"[^"]*"', m_order.group(1)))

    m_map = re.search(r"_LEGACY_TEMA_MAP\s*=\s*\{([\s\S]+?)\}", src)
    if not m_map:
        return
    # Format: "gammalt": "nytt" eller "gammalt": None
    map_targets = re.findall(r'"[^"]+"\s*:\s*"([^"]+)"', m_map.group(1))
    invalid = [t for t in map_targets if t not in tema_names]
    check(
        "_LEGACY_TEMA_MAP målteman är giltiga",
        not invalid,
        detail=f"okända målteman i legacy-map: {invalid}",
    )


def check_exclude_patterns_match_between_layers() -> None:
    """Interna admin-mönster i streamlit_app.py bör också nämnas i analyzer.py-prompten
    som sådant AI:n ska undvika (eller åtminstone inte hög-prioritera)."""
    st_src = _read("streamlit_app.py")
    ana_src = _read("analyzer.py")

    m = re.search(r"_INTERNAL_ADMIN_PATTERNS\s*=\s*\(([\s\S]+?)\)", st_src)
    if not m:
        check("_INTERNAL_ADMIN_PATTERNS hittas", False,
              "kunde inte hitta filter-listan i streamlit_app.py")
        return

    # Bara sanity-check: filtret ska INTE vara tomt
    patterns = re.findall(r'"([^"]+)"', m.group(1))
    check(
        "Interna admin-filter är inte tomt",
        len(patterns) >= 5,
        detail=f"bara {len(patterns)} mönster — troligen bortglömt",
    )

    # Kolla att analyzer.py också nämner event-filter (som redan finns där)
    ana_has_exclude = "EXCLUDE_PATTERNS" in ana_src and "STRETCH_PATTERNS" in ana_src
    check(
        "analyzer.py har event/stretch-filter",
        ana_has_exclude,
        detail="EXCLUDE_PATTERNS eller STRETCH_PATTERNS saknas i analyzer.py",
    )


def check_downgrade_prefixes_exist() -> None:
    """analyzer.py ska ha _DOWNGRADE_PREFIXES för att nedgradera Survey/Consultation."""
    ana_src = _read("analyzer.py")
    has = "_DOWNGRADE_PREFIXES" in ana_src or 'startswith(p) for p in _DOWNGRADE' in ana_src
    check(
        "_DOWNGRADE_PREFIXES finns i analyzer.py",
        has,
        detail="post-process för nedgradering saknas — hög-prio-taket riskerar brytas",
    )


def check_overrides_flow() -> None:
    """.agent_overrides.json ska läsas av både analyzer.py och streamlit_app.py."""
    ana_src = _read("analyzer.py")
    st_src = _read("streamlit_app.py")
    check(
        "analyzer.py läser .agent_overrides.json",
        "_load_relevans_overrides" in ana_src or ".agent_overrides.json" in ana_src,
        detail="prio-overrides kommer ignoreras av AI-körningen",
    )
    check(
        "html-vyn (via streamlit) kan hantera overrides",
        "overrides" in st_src.lower() or "override" in _read("output/html_report.py").lower(),
        detail="prio-overrides syns inte i visningen",
    )


def main() -> int:
    print("─ AI-prompt sync check ─")
    check_tema_order_in_prompt()
    check_refine_tema_uses_valid_temas()
    check_legacy_tema_map_targets_valid()
    check_exclude_patterns_match_between_layers()
    check_downgrade_prefixes_exist()
    check_overrides_flow()

    for line in OK:
        print(f"  {line}")
    for line in FAILED:
        print(f"  {line}")

    print()
    if FAILED:
        print(f"⚠ {len(FAILED)} sync-fel hittade — fixa innan push")
        return 1
    print(f"✓ Allt synkat ({len(OK)} kontroller passerade)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
