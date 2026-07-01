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


def _render_item_card(item: dict, auto_save_ctx: dict | None = None, key_prefix: str = "") -> None:
    """Renderar ett enda item som ett Streamlit-kort med prio-dropdown.
    auto_save_ctx (dict med root/repo/pat) triggar direkt-sparning till GitHub
    vid varje ändring. Om None faller vi tillbaka till pending-model.
    key_prefix läggs till widget-keys så samma item kan visas i flera sektioner
    utan att Streamlit klagar på dubblett-keys."""
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
                key=f"prio_{key_prefix}_{url or title}",
                label_visibility="collapsed",
            )
            new_rel = options[labels.index(new_label)]
            if new_rel != rel:
                if auto_save_ctx and auto_save_ctx.get("pat"):
                    # Autosave direkt till GitHub — ingen mellansteg
                    ok, msg = _autosave_single_change(
                        auto_save_ctx["root"], auto_save_ctx["repo"],
                        auto_save_ctx["pat"], url, new_rel,
                    )
                    if ok:
                        st.toast(f"✓ Sparat: {RELEVANS_LABEL[new_rel]}", icon="💾")
                    else:
                        st.toast(f"⚠ Kunde inte spara: {msg}", icon="⚠️")
                else:
                    # Ingen PAT → fall tillbaka till pending-model
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


def _autosave_single_change(root: Path, repo: str, pat: str, url: str, new_rel: str) -> tuple[bool, str]:
    """Sparar EN prio-ändring direkt till GitHub. Uppdaterar session_state
    committed direkt vid framgång. Använder samma säkerhetscheckar som
    batch-savefunktionen (validerar URL, whitelist på relevans, ingen PAT-läcka)."""
    if not _is_safe_url(url) or new_rel not in _VALID_RELEVANS:
        return False, "Ogiltigt värde."
    # Sätt i committed direkt (optimistic) — reverterar om PUT failar
    prev = st.session_state.overrides_committed.get(url)
    st.session_state.overrides_committed[url] = new_rel

    ok, msg = _save_to_github(root, repo, pat)
    if not ok:
        # Revertera vid fel så state matchar disk
        if prev is None:
            st.session_state.overrides_committed.pop(url, None)
        else:
            st.session_state.overrides_committed[url] = prev
    return ok, msg


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


def _items_last_24h(all_items: list[dict], cache: dict) -> list[dict]:
    """Items vars cached_at är idag eller igår (rolling 24h-fönster).
    Skyddar mot att items 'försvinner' när man kör flera gånger samma dag."""
    today = date.today().isoformat()
    yesterday = (date.today() - timedelta(days=1)).isoformat()
    ok_dates = {today, yesterday}
    result = []
    for url, entry in cache.items():
        if entry.get("cached_at", "") not in ok_dates:
            continue
        analysis = entry.get("analysis") or {}
        rel = analysis.get("relevans", "okänd")
        if rel not in ("hög", "medel"):
            continue
        # Föredra full item om det finns, fall tillbaka till cache-entryn
        by_url = {i.get("url"): i for i in all_items if i.get("url") == url}
        if url in by_url:
            result.append(by_url[url])
        else:
            result.append({
                "title": entry.get("title", ""),
                "url": url,
                "source": entry.get("source", ""),
                "type": entry.get("type", ""),
                "date": entry.get("date", ""),
                "committee": entry.get("committee", ""),
                "analysis": analysis,
            })
    return _sort_items(result)


def _render_summary_section(items_24h: list[dict]) -> None:
    """Snabb summering — de viktigaste nya puckarna från senaste 24h."""
    st.markdown("## 🆕 Nytt de senaste 24 timmarna")
    if not items_24h:
        st.caption("Inga nya puckar de senaste 24 timmarna.")
        return
    # Ta de 5 viktigaste (hög-prio först, sen datum-nyast)
    top = items_24h[:5]
    st.caption(f"{len(items_24h)} nya totalt · visar topp {len(top)}")
    for item in top:
        rel = _effective_relevans(item)
        emoji = RELEVANS_EMOJI.get(rel, "⚪")
        title = item.get("title", "Utan titel")
        source = item.get("source", "")
        d = (item.get("date") or "")[:10]
        vinkel = item.get("analysis", {}).get("tech_vinkel", "")
        url = item.get("url", "")
        meta = " · ".join([p for p in (d, source) if p])
        with st.container(border=True):
            # Escape via st.markdown-safe formatering
            st.markdown(f"**{emoji} {title}**")
            if meta:
                st.caption(meta)
            if vinkel:
                st.markdown(f"*{vinkel}*")
            if _is_safe_url(url):
                st.markdown(f"[Läs originaldokumentet →]({url})")


def _render_calendar_section(dates_data: dict, items_by_url: dict) -> None:
    """Månadskalender + kommande viktiga datum."""
    st.markdown("## 📅 Kalender")
    today = date.today()
    # Filter: bara datum från idag och 30 dagar framåt
    upcoming = {}
    for d_iso, entries in (dates_data or {}).items():
        try:
            d = date.fromisoformat(d_iso)
        except ValueError:
            continue
        if today <= d <= today + timedelta(days=60):
            upcoming[d_iso] = entries
    if not upcoming:
        st.caption("Inga inplanerade viktiga datum de närmaste 60 dagarna.")
        return
    # Sortera efter datum
    for d_iso in sorted(upcoming.keys()):
        entries = upcoming[d_iso]
        d = date.fromisoformat(d_iso)
        days_from_now = (d - today).days
        if days_from_now == 0:
            when = "**Idag**"
        elif days_from_now == 1:
            when = "**Imorgon**"
        elif days_from_now < 7:
            when = f"**Om {days_from_now} dagar**"
        else:
            when = f"Om {days_from_now} dagar"
        with st.container(border=True):
            st.markdown(f"### 📅 {d.strftime('%-d %B %Y').replace('January','januari').replace('February','februari').replace('March','mars').replace('April','april').replace('May','maj').replace('June','juni').replace('July','juli').replace('August','augusti').replace('September','september').replace('October','oktober').replace('November','november').replace('December','december')} — {when}")
            for e in entries:
                beskr = e.get("beskrivning", "")
                arende = e.get("arende", "")
                src_url = e.get("url", "")
                if arende:
                    st.markdown(f"- **{arende}** — {beskr}")
                else:
                    st.markdown(f"- {beskr}")
                if src_url:
                    st.caption(f"[Källa]({src_url})")


def _render_topics_section(arenden_data: dict, all_items: list[dict], auto_save_ctx: dict) -> None:
    """Ärenden sorterade efter senast uppdaterade. Under varje ärende visas
    de tillhörande puckarna som mindre kort."""
    st.markdown("## 🎯 Ämnen — sorterade efter senast händelse")
    if not arenden_data:
        st.caption("Inga aktiva ärenden ännu.")
        return

    # Sortera ärenden efter last_updated (nyast först)
    sorted_arenden = sorted(
        arenden_data.items(),
        key=lambda x: x[1].get("last_updated", ""),
        reverse=True,
    )

    # Bygg URL → item lookup för snabb åtkomst
    by_url = {i.get("url", ""): i for i in all_items if i.get("url")}

    for arende_name, arende_info in sorted_arenden:
        docs = arende_info.get("documents", [])
        if not docs:
            continue
        last_updated = arende_info.get("last_updated", "")
        n_docs = len(docs)
        # Räkna hög-prio-dokument i ärendet
        n_hog = 0
        for doc in docs:
            item = by_url.get(doc.get("url", ""))
            if item and _effective_relevans(item) == "hög":
                n_hog += 1
        header = f"📁 **{arende_name}** — {n_docs} dokument"
        if n_hog:
            header += f" ({n_hog} 🔴 hög)"
        header += f" · Senaste händelse: {last_updated}"
        with st.expander(header, expanded=False):
            next_step = arende_info.get("next_step", "")
            if next_step:
                st.info(f"**Nästa steg:** {next_step}")
            # Visa dokumenten (nyast först) som mindre kort
            for doc in sorted(docs, key=lambda d: d.get("date", ""), reverse=True):
                doc_url = doc.get("url", "")
                item = by_url.get(doc_url)
                if item:
                    _render_item_card(item, auto_save_ctx, key_prefix=f"topic_{arende_name}")
                else:
                    # Item saknas i memory (kanske klustrat bort) — visa minimalt
                    d = (doc.get("date") or "")[:10]
                    title = doc.get("title", "Utan titel")
                    source = doc.get("source", "")
                    tech = doc.get("tech_vinkel", "")
                    with st.container(border=True):
                        st.markdown(f"**{title}**")
                        st.caption(" · ".join([p for p in (d, source) if p]))
                        if tech:
                            st.markdown(f"*{tech}*")
                        if doc_url:
                            st.markdown(f"[Läs originaldokumentet →]({doc_url})")


def _render_uncategorized_section(all_items: list[dict], arenden_data: dict, filters: dict, auto_save_ctx: dict) -> None:
    """Puckar som INTE ligger under ett ärende — grupperade per prio."""
    st.markdown("## 📌 Övriga puckar")
    # Samla alla URL:er som redan visas under ett ärende
    used_urls = set()
    for arende_info in (arenden_data or {}).values():
        for doc in arende_info.get("documents", []):
            if doc.get("url"):
                used_urls.add(doc["url"])

    uncat = [i for i in all_items if i.get("url", "") not in used_urls]
    # Applicera filter + sortering
    uncat = _apply_filters(uncat, filters)
    if not st.session_state.show_excluded:
        uncat = [i for i in uncat if _effective_relevans(i) != "utesluten"]
    uncat = _sort_items(uncat)

    if not uncat:
        st.caption("Inga övriga puckar utanför ämneslistan.")
        return

    st.caption(f"{len(uncat)} puckar som inte hör till ett specifikt ämne")
    for item in uncat[:30]:  # begränsa initial rendering
        _render_item_card(item, auto_save_ctx, key_prefix="uncat")
    if len(uncat) > 30:
        st.caption(f"... och {len(uncat) - 30} till (filtrera i sidopanelen för att smalna av)")


def render_live_view(
    root: Path,
    memory: dict,
    cache: dict,
    arenden: dict = None,
    dates: dict = None,
    repo: str = "",
    pat: str = "",
) -> None:
    """Huvudrenderare — anropas från streamlit_app.py inuti Live-fliken.

    Layout:
    1. Header med stats + osparade ändringar-flärp
    2. Sidopanel-filter
    3. 🆕 Nytt de senaste 24h (topp 5)
    4. 📅 Kalender med kommande viktiga datum
    5. 🎯 Ämnen — ärenden sorterade efter senast uppdaterade
    6. 📌 Övriga puckar — items utanför ärendelistan
    """
    _init_state(root)
    arenden = arenden or {}
    dates = dates or {}

    all_items = _flatten_items(memory, cache)
    items_by_url = {i.get("url", ""): i for i in all_items if i.get("url")}

    # ── Sidopanel-filter ─────────────────────────
    st.sidebar.markdown("### 🔴 Live-vy filter")
    search = st.sidebar.text_input("🔎 Sök", key="lv_search")
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

    # Auto-save kontext skickas till varje kort så prio-ändringar direktsparas
    auto_save_ctx = {"root": root, "repo": repo, "pat": pat}
    if not pat:
        st.warning(
            "⚠ GITHUB_PAT saknas i Streamlit secrets — prio-ändringar sparas bara "
            "i denna webbläsar-session tills PAT konfigureras."
        )

    st.caption("💡 Ändra prioritet på ett ärende sparas direkt. AI:n läser dina val och lär sig av mönstret framöver.")
    st.divider()

    # ── 1. Nytt de senaste 24h ───────────────────
    items_24h = _items_last_24h(all_items, cache)
    _render_summary_section(items_24h)

    st.divider()

    # ── 2. Kalender ──────────────────────────────
    _render_calendar_section(dates, items_by_url)

    st.divider()

    # ── 3. Ämnen (per ärende, sorterade efter senast händelse) ──
    _render_topics_section(arenden, all_items, auto_save_ctx)

    st.divider()

    # ── 4. Övriga puckar (utan tillhörande ärende) ──
    filters = {"search": search, "prios": prio_choices, "sources": source_choices}
    _render_uncategorized_section(all_items, arenden, filters, auto_save_ctx)
