"""
Live-vy — bygger ärendekortet direkt i Streamlit istället för att renderas
in i en statisk HTML-rapport. Fördel: UX-ändringar syns direkt, ingen
"regenerera HTML"-mellansteg. Priset: vi tappar en del av HTML-rapportens
finlemmade CSS/layout, men det får vi tillbaka med Streamlits nativa widgets.

Läser data direkt från:
  .agent_memory.json           (dagsuppdelade items)
  .agent_analysis_cache.json   (analyserade items med URL-nyckel)
  .agent_overrides.json        (manuella prio-ändringar)

Skriver till:
  .agent_overrides.json        (via GitHub Contents API när användaren
                                trycker "Spara till repo")
"""
from __future__ import annotations
import json
import base64
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import requests
import streamlit as st

RELEVANS_EMOJI = {"hög": "🔴", "medel": "🟡", "låg": "🟢", "okänd": "⚪", "utesluten": "🚫"}
RELEVANS_LABEL = {
    "hög": "Hög prioritet", "medel": "Medel", "låg": "Låg",
    "okänd": "Okänd", "utesluten": "Utesluten från rapport",
}
RELEVANS_COLOR = {"hög": "#c0392b", "medel": "#d68910", "låg": "#27ae60",
                  "okänd": "#888", "utesluten": "#6b7280"}
RELEVANS_ORDER = {"hög": 0, "medel": 1, "låg": 2, "okänd": 3, "utesluten": 4}

# I session_state:
#   overrides_pending: dict[url, new_relevans] — ändringar sedan senaste spara
#   overrides_committed: dict[url, relevans] — laddade från .agent_overrides.json
#   show_excluded: bool — visa uteslutna eller ej


def _load_overrides_from_file(root: Path) -> dict[str, str]:
    """Läser .agent_overrides.json från lokala repo:t (det som är checkat ut)."""
    path = root / ".agent_overrides.json"
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        result = {}
        for url, val in data.items():
            if isinstance(val, str):
                result[url] = val
            elif isinstance(val, dict) and "relevans" in val:
                result[url] = val["relevans"]
        return result
    except Exception:
        return {}


def _init_state(root: Path) -> None:
    """Initierar session_state en gång per user-session."""
    if "overrides_committed" not in st.session_state:
        st.session_state.overrides_committed = _load_overrides_from_file(root)
    if "overrides_pending" not in st.session_state:
        st.session_state.overrides_pending = {}
    if "show_excluded" not in st.session_state:
        st.session_state.show_excluded = False


def _effective_relevans(item: dict) -> str:
    """Returnerar den relevansnivå som ska visas — pending override tar företräde
    över committed, som tar företräde över AI:ns bedömning."""
    url = item.get("url", "")
    if url in st.session_state.overrides_pending:
        return st.session_state.overrides_pending[url]
    if url in st.session_state.overrides_committed:
        return st.session_state.overrides_committed[url]
    return item.get("analysis", {}).get("relevans", "okänd")


def _is_manually_set(item: dict) -> bool:
    url = item.get("url", "")
    return url in st.session_state.overrides_pending or url in st.session_state.overrides_committed


def _flatten_items(memory: dict[str, list], cache: dict[str, dict]) -> list[dict]:
    """Kombinerar memory (dagsuppdelat) + cache (URL-uppdelat) till en flat lista.
    Dedupar på URL — memory:s version vinner (har mer metadata typiskt)."""
    seen_urls = set()
    result = []
    for day_items in memory.values():
        for it in day_items:
            url = it.get("url", "")
            if url in seen_urls:
                continue
            if url:
                seen_urls.add(url)
            result.append(it)
    # Lägg till cache-items som inte redan finns
    for url, entry in cache.items():
        if url in seen_urls or not url:
            continue
        seen_urls.add(url)
        result.append({
            "title": entry.get("title", ""),
            "url": url,
            "source": entry.get("source", ""),
            "type": entry.get("type", ""),
            "date": entry.get("date", ""),
            "committee": entry.get("committee", ""),
            "summary": entry.get("summary", ""),
            "analysis": entry.get("analysis", {}),
        })
    return result


def _apply_filters(items: list[dict], filters: dict) -> list[dict]:
    """Applicerar sidopanel-filter: prio, källa, textsökning."""
    q = (filters.get("search") or "").strip().lower()
    prios = set(filters.get("prios") or [])
    sources = set(filters.get("sources") or [])

    def _match(it: dict) -> bool:
        rel = _effective_relevans(it)
        if prios and rel not in prios:
            return False
        if sources and it.get("source", "") not in sources:
            return False
        if q:
            haystack = " ".join([
                it.get("title", ""),
                it.get("analysis", {}).get("sammanfattning", ""),
                it.get("analysis", {}).get("tech_vinkel", ""),
                it.get("analysis", {}).get("arende", "") or "",
                " ".join(it.get("analysis", {}).get("keywords") or []),
            ]).lower()
            if q not in haystack:
                return False
        return True

    return [i for i in items if _match(i)]


def _sort_items(items: list[dict]) -> list[dict]:
    """Nyast först. Items med samma datum sorteras på hög-medel-låg."""
    def _key(i):
        d = (i.get("date") or "")[:10]
        rel_ord = RELEVANS_ORDER.get(_effective_relevans(i), 99)
        # Negera datum-strängen så nyast först (sträng-sort baklänges)
        return (d, -rel_ord)
    return sorted(items, key=_key, reverse=True)


_VALID_RELEVANS = {"hög", "medel", "låg", "utesluten"}


def _is_safe_url(url: str) -> bool:
    """URL måste vara http(s)://. Skyddar mot javascript:/file:/data: i overrides.json."""
    if not url or not isinstance(url, str):
        return False
    s = url.strip()
    return s.startswith(("http://", "https://")) and len(s) < 2000


def _change_prio(url: str, new_rel: str) -> None:
    """Registrerar en pending ändring. Sparas till fil när användaren trycker spara.
    Validerar både URL och relevans-värdet."""
    if not _is_safe_url(url) or new_rel not in _VALID_RELEVANS:
        return
    committed = st.session_state.overrides_committed.get(url)
    if new_rel == committed:
        # Ingen ändring mot committed — ta bort ur pending
        st.session_state.overrides_pending.pop(url, None)
    else:
        st.session_state.overrides_pending[url] = new_rel


def _render_item_card(item: dict) -> None:
    """Renderar ett enda item som ett Streamlit-kort med prio-dropdown."""
    url = item.get("url", "")
    rel = _effective_relevans(item)
    manual = _is_manually_set(item)
    a = item.get("analysis", {})
    title = item.get("title", "Utan titel")
    source = item.get("source", "")
    committee = item.get("committee", "")
    item_type = item.get("type", "")
    d = (item.get("date") or "")[:10]

    color = RELEVANS_COLOR.get(rel, "#888")
    with st.container(border=True):
        # Header-rad: prio-dropdown | titel | manuell-markering
        cols = st.columns([0.24, 0.7, 0.06])
        with cols[0]:
            options = ["hög", "medel", "låg", "utesluten"]
            labels = [f"{RELEVANS_EMOJI[o]} {RELEVANS_LABEL[o]}" for o in options]
            try:
                current_idx = options.index(rel)
            except ValueError:
                # rel är "okänd" — visa som medel-slot
                current_idx = 1
            new_label = st.selectbox(
                "Prioritet",
                labels,
                index=current_idx,
                key=f"prio_{url or title}",
                label_visibility="collapsed",
            )
            new_rel = options[labels.index(new_label)]
            if new_rel != rel:
                _change_prio(url, new_rel)
                st.rerun()
        with cols[1]:
            emoji = RELEVANS_EMOJI.get(rel, "⚪")
            st.markdown(f"### {emoji} {title}")
            meta_parts = [d, source, item_type, committee]
            meta = " · ".join([p for p in meta_parts if p])
            if meta:
                st.caption(meta)
        with cols[2]:
            if manual:
                st.caption("✏️")

        # Body: sammanfattning + tech-vinkel + varför + länk
        samm = a.get("sammanfattning", "")
        vinkel = a.get("tech_vinkel", "")
        varfor = a.get("varfor_viktigt", "")
        eu_koppling = a.get("eu_koppling") or ""
        arende = a.get("arende") or ""

        if samm:
            st.markdown(f"**Vad handlar det om?** {samm}")
        if vinkel:
            st.markdown(f"**Tech-vinkel:** {vinkel}")
        if varfor:
            st.markdown(f"**Varför viktigt:** {varfor}")
        if eu_koppling and eu_koppling != "null":
            st.markdown(f"🇪🇺 **EU-koppling:** {eu_koppling}")
        if arende:
            st.caption(f"📁 Ärende: {arende}")
        if url:
            st.markdown(f"[Läs originaldokumentet →]({url})")


_HTTP_STATUS_HINTS = {
    401: "Ogiltig eller utgången GITHUB_PAT.",
    403: "Otillräckliga rättigheter på PAT (behöver 'Contents: write' på repot).",
    404: "Repo hittades inte — kontrollera GITHUB_REPO-inställningen.",
    422: "GitHub avvisade payload (fel format).",
}


def _save_to_github(root: Path, repo: str, pat: str) -> tuple[bool, str]:
    """Skriver .agent_overrides.json till GitHub via Contents API.
    PAT skickas ENDAST i Authorization-header och läcker aldrig i felmeddelanden
    till användaren (bara HTTP-status + en generisk förklaring)."""
    if not pat:
        return False, "GITHUB_PAT saknas i Streamlit secrets — kan inte skriva till GitHub."
    # Slå ihop committed + pending. Validera varje entry innan skrivning.
    merged: dict[str, str] = {}
    for src in (st.session_state.overrides_committed, st.session_state.overrides_pending):
        for url, val in src.items():
            if _is_safe_url(url) and val in _VALID_RELEVANS:
                merged[url] = val
    new_content = json.dumps(merged, ensure_ascii=False, indent=2) + "\n"

    api = f"https://api.github.com/repos/{repo}/contents/.agent_overrides.json"
    headers = {"Authorization": f"Bearer {pat}", "Accept": "application/vnd.github+json"}
    # Hämta ev befintlig fils SHA (behövs för uppdatering)
    sha = None
    try:
        r = requests.get(api, headers=headers, timeout=10)
    except requests.RequestException as e:
        return False, f"Nätverksfel mot GitHub: {type(e).__name__}"
    if r.status_code == 200:
        sha = r.json().get("sha")
    elif r.status_code != 404:
        hint = _HTTP_STATUS_HINTS.get(r.status_code, "")
        return False, f"Kunde inte läsa befintlig fil (HTTP {r.status_code}). {hint}"

    payload = {
        "message": f"Prio-ändringar från Live-vy ({len(st.session_state.overrides_pending)} nya)",
        "content": base64.b64encode(new_content.encode("utf-8")).decode("ascii"),
    }
    if sha:
        payload["sha"] = sha
    try:
        r = requests.put(api, headers=headers, json=payload, timeout=15)
    except requests.RequestException as e:
        return False, f"Nätverksfel mot GitHub: {type(e).__name__}"

    if r.status_code in (200, 201):
        st.session_state.overrides_committed = merged
        st.session_state.overrides_pending = {}
        try:
            (root / ".agent_overrides.json").write_text(new_content, encoding="utf-8")
        except Exception:
            pass  # icke-kritiskt: appen använder session_state framöver
        return True, f"✓ {len(merged)} prio-ändringar sparade till GitHub."
    hint = _HTTP_STATUS_HINTS.get(r.status_code, "")
    return False, f"GitHub PUT misslyckades (HTTP {r.status_code}). {hint}"


def render_live_view(
    root: Path,
    memory: dict,
    cache: dict,
    repo: str,
    pat: str,
) -> None:
    """Huvudrenderare — anropas från streamlit_app.py inuti Live-fliken."""
    _init_state(root)

    all_items = _flatten_items(memory, cache)

    # ── Sidopanel-filter ─────────────────────────
    st.sidebar.markdown("### 🔴 Live-vy filter")
    search = st.sidebar.text_input("🔎 Sök i titel/sammanfattning/ärende", key="lv_search")
    prio_choices = st.sidebar.multiselect(
        "Prioritet",
        ["hög", "medel", "låg"],
        default=["hög", "medel"],
        format_func=lambda p: f"{RELEVANS_EMOJI[p]} {RELEVANS_LABEL[p]}",
        key="lv_prios",
    )
    all_sources = sorted({i.get("source", "") for i in all_items if i.get("source")})
    source_choices = st.sidebar.multiselect(
        "Källa (tom = alla)",
        all_sources,
        default=[],
        key="lv_sources",
    )
    st.session_state.show_excluded = st.sidebar.checkbox(
        "Visa uteslutna", value=st.session_state.show_excluded, key="lv_show_ex"
    )

    # ── Toppheader med stats ─────────────────────
    total = len(all_items)
    counts = {"hög": 0, "medel": 0, "låg": 0, "utesluten": 0}
    for it in all_items:
        r = _effective_relevans(it)
        if r in counts:
            counts[r] += 1

    top_cols = st.columns([1, 1, 1, 1, 2])
    top_cols[0].metric("📋 Totalt", total)
    top_cols[1].metric("🔴 Hög", counts["hög"])
    top_cols[2].metric("🟡 Medel", counts["medel"])
    top_cols[3].metric("🟢 Låg", counts["låg"])
    if counts["utesluten"]:
        top_cols[4].metric("🚫 Uteslutna", counts["utesluten"])

    # ── Osparade ändringar-flärp ─────────────────
    n_pending = len(st.session_state.overrides_pending)
    if n_pending:
        c1, c2 = st.columns([3, 1])
        with c1:
            st.info(f"✏️ {n_pending} osparade prio-ändringar")
        with c2:
            if st.button("💾 Spara till GitHub", type="primary", key="lv_save"):
                ok, msg = _save_to_github(root, repo, pat)
                if ok:
                    st.success(msg)
                    st.rerun()
                else:
                    st.error(msg)

    # ── Filter + sortering ───────────────────────
    filters = {"search": search, "prios": prio_choices, "sources": source_choices}
    filtered = _apply_filters(all_items, filters)
    # Uteslut-hantering
    if not st.session_state.show_excluded:
        n_hidden = sum(1 for i in filtered if _effective_relevans(i) == "utesluten")
        filtered = [i for i in filtered if _effective_relevans(i) != "utesluten"]
        if n_hidden:
            st.caption(f"🚫 {n_hidden} uteslutna göms — bocka i 'Visa uteslutna' i sidopanelen för att se dem")

    sorted_items = _sort_items(filtered)

    st.markdown(f"### {len(sorted_items)} ärenden efter filter")

    if not sorted_items:
        st.info("Inga ärenden matchar filtren. Prova att bocka i fler prioriteter eller rensa sökrutan.")
        return

    for item in sorted_items:
        _render_item_card(item)
