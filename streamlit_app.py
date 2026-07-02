"""
Streamlit-app som visar de senaste tech-vinkel-rapporterna.

Lokal körning:  streamlit run streamlit_app.py
Streamlit Cloud: pekar på samma repo, läser reports/ direkt.
"""
import json
import os
from pathlib import Path
from datetime import datetime, date

import requests
import streamlit as st

GITHUB_REPO = "EEGalli/tech-vinklar-agent"
WORKFLOW_FILE = "run-agent.yml"

ROOT = Path(__file__).parent
REPORTS_DIR = ROOT / "reports"
MEMORY_FILE = ROOT / ".agent_memory.json"

# Källa → vilken flik de hör hemma i
SOURCE_TO_TAB = {
    "Riksdagen": "Riksdagen",
    "Regeringen": "Regeringen",
    "EU-parlamentet": "EU-parlamentet",
    "EU-kommissionen": "EU-kommissionen",
    # Alla byrå-källor (förkortningar) → "EU-byråer"
    "ENISA": "EU-byråer",
    "EDPB": "EU-byråer",
    "BEREC": "EU-byråer",
    "ESMA": "EU-byråer",
    "Europol": "EU-byråer",
    "EBA": "EU-byråer",
    "EMA": "EU-byråer",
    "ACER": "EU-byråer",
    "CEDEFOP": "EU-byråer",
    "EUSPA": "EU-byråer",
    "Eurojust": "EU-byråer",
    "EU-OSHA": "EU-byråer",
    "Frontex": "EU-byråer",
    # Svenska myndigheter (separat från departement)
    "IMY": "Svenska myndigheter",
    "MSB": "Svenska myndigheter",
    # Tech-policy-media (sekundärkällor)
    "EDRi": "Tech-media",
    "Politico EU Tech": "Tech-media",
}

RELEVANS_EMOJI = {"hög": "🔴", "medel": "🟡", "låg": "🟢", "okänd": "⚪", "utesluten": "🚫"}

st.set_page_config(
    page_title="Tech Vinklar — EU & Riksdagen",
    page_icon="🔍",
    layout="wide",
    initial_sidebar_state="collapsed",  # mer plats på bredden — kontroller ligger i topbaren
)


def _list_reports() -> list[Path]:
    """Returnerar alla digest-rapporter sorterade nyast först."""
    if not REPORTS_DIR.exists():
        return []
    return sorted(REPORTS_DIR.rglob("digest_*.html"), reverse=True)


def _format_label(p: Path) -> str:
    """Formaterar 'digest_20260427_0808.html' → '27 apr 2026, 10:08' (svensk tid).
    Filnamnen är UTC (både lokala körningar och GitHub Actions stämplar UTC),
    så vi konverterar till Europe/Stockholm för visning."""
    from datetime import timezone
    try:
        from zoneinfo import ZoneInfo
        stockholm = ZoneInfo("Europe/Stockholm")
    except Exception:
        stockholm = None  # fallback: visa UTC om zoneinfo saknas
    name = p.stem.replace("digest_", "").replace("_rebuild", "").replace("_nytt", "")
    parts = name.split("_")
    if len(parts) >= 2:
        try:
            dt_utc = datetime.strptime(f"{parts[0]}_{parts[1][:4]}", "%Y%m%d_%H%M")
            dt_utc = dt_utc.replace(tzinfo=timezone.utc)
            dt_local = dt_utc.astimezone(stockholm) if stockholm else dt_utc
            return dt_local.strftime("%-d %b %Y, %H:%M")
        except ValueError:
            pass
    return p.name


def _load_all_items() -> list[dict]:
    """Läser alla items från senaste 30 dagars memory + samlar dem."""
    if not MEMORY_FILE.exists():
        return []
    try:
        with open(MEMORY_FILE, encoding="utf-8") as f:
            mem = json.load(f)
    except Exception:
        return []
    seen_urls: set[str] = set()
    out: list[dict] = []
    # Iterera nyaste dag först → en URL hamnar med nyaste analysen
    for day in sorted(mem.keys(), reverse=True):
        for item in mem[day]:
            url = item.get("url") or ""
            key = url or item.get("title", "")
            if key in seen_urls:
                continue
            seen_urls.add(key)
            out.append(item)
    return out


def _date_int(s: str) -> int:
    if not s:
        return 0
    try:
        return int(s[:10].replace("-", ""))
    except ValueError:
        # Hantera RSS-format som "Tue, 21 Apr 2026"
        try:
            d = datetime.strptime(s[:16], "%a, %d %b %Y")
            return int(d.strftime("%Y%m%d"))
        except Exception:
            return 0


def _render_item(item: dict) -> None:
    """Renderar ett item som ett synligt kort med all info direkt."""
    a = item.get("analysis", {})
    title = item.get("title", "Utan titel")
    relevans = a.get("relevans", "okänd")
    emoji = RELEVANS_EMOJI.get(relevans, "⚪")
    date_str = (item.get("date") or "")[:10]
    source = item.get("source", "")
    item_type = item.get("type", "")
    url = item.get("url", "")

    with st.container(border=True):
        st.markdown(f"### {emoji} {title}")
        meta = " · ".join(filter(None, [date_str, source, item_type]))
        if meta:
            st.caption(meta)
        if a.get("sammanfattning"):
            st.markdown(f"**Vad handlar det om?** {a['sammanfattning']}")
        if a.get("tech_vinkel"):
            st.markdown(f"**Tech-vinkel:** {a['tech_vinkel']}")
        if a.get("varfor_viktigt"):
            st.markdown(f"**Varför viktigt:** {a['varfor_viktigt']}")
        if a.get("eu_koppling") and a["eu_koppling"] != "null":
            st.markdown(f"🇪🇺 **EU-koppling:** {a['eu_koppling']}")
        if url:
            st.markdown(f"[Läs originaldokumentet →]({url})")


# ── Horisontell topbar (ersätter sidopanelen så vi får plats på bredden) ──
reports = _list_reports()

if not reports:
    st.warning("Inga rapporter hittades. Kör `python3 main.py` lokalt först.")
    st.stop()

labels = [_format_label(p) for p in reports]

# En rad: titel · rapport-väljare · sök · kör-knapp · uppdatera-knapp
_top = st.container()
with _top:
    _c_title, _c_report, _c_search, _c_run, _c_refresh = st.columns([2, 3, 3, 2, 1])
    with _c_title:
        st.markdown("### 🔍 Tech Vinklar")
        st.caption(f"{len(reports)} rapporter · senaste {labels[0]}")
    with _c_report:
        selected_idx = st.selectbox(
            "Välj rapport",
            options=range(len(reports)),
            format_func=lambda i: labels[i],
            index=0,
            key="tb_report",
        )
    with _c_search:
        search_query = st.text_input(
            "🔎 Sök i titel/sammanfattning/vinkel",
            placeholder="t.ex. AI Act, biometri, NIS2",
            key="tb_search",
        )
    with _c_run:
        st.markdown("&nbsp;", unsafe_allow_html=True)  # aligning knapp med input-fält
        trigger = st.button("🔄 Kör ny analys", use_container_width=True, key="tb_trigger")
    with _c_refresh:
        st.markdown("&nbsp;", unsafe_allow_html=True)
        refresh = st.button("↻", use_container_width=True, help="Uppdatera körstatus", key="tb_refresh")

selected = reports[selected_idx]

def _get_pat() -> str:
    try:
        return st.secrets.get("GITHUB_PAT", "") if hasattr(st, "secrets") else ""
    except Exception:
        # Ingen secrets.toml (t.ex. lokal körning) → ingen PAT, men appen ska inte krascha.
        return ""


# ── Prioritet-overrides: ladda + spara (server-side, ingen token i webbläsaren) ──
OVERRIDES_FILE = ROOT / ".agent_overrides.json"
VALID_RELEVANS = {"hög", "medel", "låg", "utesluten"}


def _is_safe_override_url(u: str) -> bool:
    return isinstance(u, str) and u.strip().startswith(("http://", "https://")) and len(u) < 2000


def _load_overrides_file() -> dict[str, str]:
    """Läser .agent_overrides.json (str-värden) från repo:t. Tål äldre dict-format."""
    if not OVERRIDES_FILE.exists():
        return {}
    try:
        data = json.loads(OVERRIDES_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}
    out: dict[str, str] = {}
    for url, val in (data or {}).items():
        if isinstance(val, str):
            out[url] = val
        elif isinstance(val, dict) and isinstance(val.get("relevans"), str):
            out[url] = val["relevans"]
    return out


def _save_override(url: str, new_rel: str) -> tuple[bool, str]:
    """Sparar EN prio-ändring: uppdaterar session + lokal fil + (om PAT finns) GitHub.
    PAT stannar server-side och läcker aldrig till webbläsaren eller felmeddelanden."""
    if not _is_safe_override_url(url) or new_rel not in VALID_RELEVANS:
        return False, "Ogiltigt värde"

    # Slå ihop mot filen OCH sessionen så inga befintliga val tappas bort vid lokal skrivning.
    merged = {**_load_overrides_file(), **st.session_state.get("live_overrides", {})}
    merged[url] = new_rel
    st.session_state["live_overrides"] = merged

    new_content = json.dumps(merged, ensure_ascii=False, indent=2) + "\n"
    # Skriv lokalt först (fungerar vid lokal körning; på Streamlit Cloud är det flyktigt
    # men ofarligt — GitHub är den beständiga lagringen).
    try:
        OVERRIDES_FILE.write_text(new_content, encoding="utf-8")
    except Exception:
        pass

    pat = _get_pat()
    if not pat:
        return True, "Sparat lokalt (GITHUB_PAT saknas — inte pushat till repo)"

    import base64 as _b64
    api = f"https://api.github.com/repos/{GITHUB_REPO}/contents/.agent_overrides.json"
    headers = {"Authorization": f"Bearer {pat}", "Accept": "application/vnd.github+json"}
    try:
        r = requests.get(api, headers=headers, timeout=10)
        sha = r.json().get("sha") if r.status_code == 200 else None
        if r.status_code not in (200, 404):
            return False, f"GitHub GET fel (HTTP {r.status_code})"
        # Slå ihop mot repo-versionen så samtidiga ändringar inte skrivs över
        repo_data: dict[str, str] = {}
        if r.status_code == 200:
            try:
                repo_data = json.loads(_b64.b64decode(r.json()["content"]).decode())
            except Exception:
                repo_data = {}
        full = {**repo_data, **merged}
        full = {k: v for k, v in full.items() if _is_safe_override_url(k) and v in VALID_RELEVANS}
        full_content = json.dumps(full, ensure_ascii=False, indent=2) + "\n"
        payload = {
            "message": "Prio-ändring från Live-vyn",
            "content": _b64.b64encode(full_content.encode("utf-8")).decode("ascii"),
        }
        if sha:
            payload["sha"] = sha
        r = requests.put(api, headers=headers, json=payload, timeout=15)
        if r.status_code in (200, 201):
            st.session_state["live_overrides"] = full
            try:
                OVERRIDES_FILE.write_text(full_content, encoding="utf-8")
            except Exception:
                pass
            return True, "Sparat till repo"
        return False, f"GitHub PUT fel (HTTP {r.status_code})"
    except requests.RequestException as e:
        return False, f"Nätverksfel: {type(e).__name__}"


_NV_PRIOS = ["hög", "medel", "låg", "utesluten"]
_NV_LABEL = {"hög": "Hög", "medel": "Medel", "låg": "Låg", "utesluten": "Utesluten"}
_NV_RANK = {"hög": 0, "medel": 1, "låg": 2, "okänd": 3, "utesluten": 4}
_NV_COLOR = {"hög": "#c0392b", "medel": "#d68910", "låg": "#27ae60", "utesluten": "#6b7280"}


def _render_native_live(items: list[dict]) -> None:
    """Nativ Live-vy (TEST): varje kort har en prio-dropdown direkt på kortet.
    Ändring sparas server-side via _save_override — ingen sökning, ingen kopiering.
    Ser ut som Streamlit-kort (inte den exakta HTML-dashboarden)."""
    import output.html_report as _hr

    def _clean(it: dict) -> str:
        try:
            return _hr._clean_title(it)
        except Exception:
            return it.get("title") or "Utan titel"

    counts = {"hög": 0, "medel": 0, "låg": 0}
    for it in items:
        r = it.get("analysis", {}).get("relevans", "okänd")
        if r in counts:
            counts[r] += 1
    m = st.columns(4)
    m[0].metric("📋 Visade", len(items))
    m[1].metric("🔴 Hög", counts["hög"])
    m[2].metric("🟡 Medel", counts["medel"])
    m[3].metric("🟢 Låg", counts["låg"])
    st.caption("💡 Ändra prioritet direkt på kortet — sparas automatiskt, ingen sökning.")
    st.divider()

    # Sortera: nyast datum först, hög-prio före inom samma datum
    ordered = sorted(
        items,
        key=lambda it: ((it.get("date") or "")[:10],
                        -_NV_RANK.get(it.get("analysis", {}).get("relevans", "okänd"), 3)),
        reverse=True,
    )

    for it in ordered:
        url = it.get("url", "")
        a = it.get("analysis", {})
        rel = a.get("relevans", "okänd")
        rel = rel if rel in _NV_PRIOS else "medel"
        color = _NV_COLOR.get(rel, "#888")
        with st.container(border=True):
            ccol, tcol = st.columns([0.2, 0.8])
            with ccol:
                new = st.selectbox(
                    "Prioritet",
                    _NV_PRIOS,
                    index=_NV_PRIOS.index(rel),
                    format_func=lambda p: f"{RELEVANS_EMOJI.get(p, '⚪')} {_NV_LABEL[p]}",
                    key=f"nv_prio_{url}",
                    label_visibility="collapsed",
                )
                if new != rel:
                    ok, msg = _save_override(url, new)
                    st.toast(f"✓ Sparat: {_NV_LABEL[new]}" if ok else f"⚠ {msg}",
                             icon="💾" if ok else "⚠️")
                    st.rerun()
            with tcol:
                st.markdown(
                    f"<span style='background:{color};color:#fff;padding:1px 8px;"
                    f"border-radius:10px;font-size:0.72rem;font-weight:600'>"
                    f"{_NV_LABEL[rel].upper()}</span>", unsafe_allow_html=True)
                st.markdown(f"##### {_clean(it)}")
                meta = " · ".join([p for p in [(it.get("date") or "")[:10],
                                               it.get("source", ""), it.get("committee", "")] if p])
                if meta:
                    st.caption(meta)
            if a.get("sammanfattning"):
                st.markdown(f"**Vad handlar det om?** {a['sammanfattning']}")
            if a.get("tech_vinkel"):
                st.markdown(f"**Tech-vinkel:** {a['tech_vinkel']}")
            if a.get("varfor_viktigt"):
                st.markdown(f"**Varför viktigt:** {a['varfor_viktigt']}")
            if a.get("eu_koppling") and a["eu_koppling"] != "null":
                st.markdown(f"🇪🇺 **EU-koppling:** {a['eu_koppling']}")
            if url:
                st.markdown(f"[Läs originaldokumentet →]({url})")


def _latest_run_status(pat: str) -> dict | None:
    """Hämtar status på den senaste workflow-körningen."""
    url = f"https://api.github.com/repos/{GITHUB_REPO}/actions/workflows/{WORKFLOW_FILE}/runs?per_page=1"
    try:
        r = requests.get(
            url,
            headers={
                "Authorization": f"Bearer {pat}",
                "Accept": "application/vnd.github+json",
            },
            timeout=8,
        )
        if r.status_code != 200:
            return None
        runs = r.json().get("workflow_runs", [])
        return runs[0] if runs else None
    except Exception:
        return None


def _get_run_steps(pat: str, run_id: int) -> list[dict]:
    """Hämtar varje steg i den körning som är igång (eller senast klar)."""
    url = f"https://api.github.com/repos/{GITHUB_REPO}/actions/runs/{run_id}/jobs"
    try:
        r = requests.get(
            url,
            headers={
                "Authorization": f"Bearer {pat}",
                "Accept": "application/vnd.github+json",
            },
            timeout=8,
        )
        if r.status_code != 200:
            return []
        jobs = r.json().get("jobs", [])
        if not jobs:
            return []
        # Vi har bara ett job ("run-agent"), ta dess steps
        return jobs[0].get("steps", [])
    except Exception:
        return []


def _step_emoji(step: dict) -> str:
    status = step.get("status", "")
    conclusion = step.get("conclusion", "")
    if status == "in_progress":
        return "⏳"
    if status == "queued":
        return "⏸"
    if status == "completed":
        if conclusion == "success":
            return "✓"
        if conclusion == "skipped":
            return "↷"
        return "✗"
    return "·"


if trigger:
    pat = _get_pat()
    if not pat:
        st.error("GITHUB_PAT saknas i Streamlit secrets.")
    else:
        url = f"https://api.github.com/repos/{GITHUB_REPO}/actions/workflows/{WORKFLOW_FILE}/dispatches"
        try:
            r = requests.post(
                url,
                headers={
                    "Authorization": f"Bearer {pat}",
                    "Accept": "application/vnd.github+json",
                    "X-GitHub-Api-Version": "2022-11-28",
                },
                json={"ref": "main"},
                timeout=10,
            )
            if r.status_code == 204:
                # Spara klick-tidpunkten i UTC ISO-format för att jämföra med run.created_at
                st.session_state["dispatch_at"] = datetime.utcnow().isoformat()
                st.success("✓ Körning startad!")
            else:
                st.error(f"Fel ({r.status_code}): {r.text[:200]}")
        except Exception as e:
            st.error(f"Anslutningsfel: {e}")

# Status-rad — visas som en enda tunn info-rad, inte i sidopanelen
pat = _get_pat()
dispatch_at = st.session_state.get("dispatch_at", "")
if pat:
    run = _latest_run_status(pat)
    if run:
        status = run.get("status", "")
        conclusion = run.get("conclusion", "")
        run_created = run.get("created_at", "")
        started_label = run_created[:16].replace("T", " ")
        run_url = run.get("html_url", "")

        is_new_run = bool(dispatch_at and run_created and run_created >= dispatch_at)
        waiting_for_new = bool(dispatch_at and not is_new_run)

        if waiting_for_new:
            st.info("⏳ Körning startas… GitHub registrerar den om ~10 sek. Tryck ↻ om en stund.")
        elif status == "completed" and conclusion == "success":
            st.caption(f"✓ Senaste körning klar {started_label}")
        elif status == "completed":
            st.error(f"✗ Senaste körning misslyckades: {conclusion} · [Loggar]({run_url})")
        elif status in ("queued", "in_progress", "waiting", "requested"):
            from datetime import timezone
            try:
                started = datetime.fromisoformat(run_created.replace("Z", "+00:00"))
                elapsed = datetime.now(timezone.utc) - started
                mins, secs = divmod(int(elapsed.total_seconds()), 60)
                duration = f"{mins}m {secs}s"
            except Exception:
                duration = "?"
            _status_msg = f"⏳ Körning pågår ({status}) — körtid {duration}"
            steps = _get_run_steps(pat, run["id"])
            if steps:
                step_lines = []
                for s in steps:
                    name = s.get("name", "")
                    if name in ("Set up job", "Post job cleanup", "Complete job"):
                        continue
                    step_lines.append(f"{_step_emoji(s)} {name}")
                if step_lines:
                    _status_msg += " · " + " · ".join(step_lines)
            st.info(_status_msg)

# ── Huvudvy: tabbar ──────────────────────────────────────
all_items = _load_all_items()
all_items.sort(key=lambda i: _date_int(i.get("date", "")), reverse=True)


def _matches_search(item: dict, q: str) -> bool:
    if not q:
        return True
    q_low = q.lower()
    a = item.get("analysis", {})
    haystack = " ".join([
        item.get("title", ""),
        a.get("sammanfattning", "") or "",
        a.get("tech_vinkel", "") or "",
        a.get("varfor_viktigt", "") or "",
        a.get("eu_koppling", "") or "",
        item.get("source", ""),
    ]).lower()
    return q_low in haystack


def _filter(tab: str) -> list[dict]:
    return [
        i for i in all_items
        if SOURCE_TO_TAB.get(i.get("source", "")) == tab
        and _matches_search(i, search_query)
    ]


riksdagen_items = _filter("Riksdagen")
regeringen_items = _filter("Regeringen")
se_myndigheter_items = _filter("Svenska myndigheter")
ep_items = _filter("EU-parlamentet")
ek_items = _filter("EU-kommissionen")
agency_items = _filter("EU-byråer")
media_items = _filter("Tech-media")

if search_query:
    total = (len(riksdagen_items) + len(regeringen_items) + len(se_myndigheter_items)
             + len(ep_items) + len(ek_items) + len(agency_items) + len(media_items))
    st.caption(f"🔎 {total} träffar för \"{search_query}\"")

(tab_live, tab_riksdag, tab_reg, tab_myn,
 tab_ep, tab_ek, tab_byraer, tab_media) = st.tabs([
    "🔴 Live",
    f"🇸🇪 Riksdagen ({len(riksdagen_items)})",
    f"🏛️ Regeringen ({len(regeringen_items)})",
    f"🏤 SE-myndigheter ({len(se_myndigheter_items)})",
    f"🇪🇺 EU-parlamentet ({len(ep_items)})",
    f"🇪🇺 EU-kommissionen ({len(ek_items)})",
    f"🏢 EU-byråer ({len(agency_items)})",
    f"📰 Tech-media ({len(media_items)})",
])

with tab_live:
    # Live-vyn = HTML-rapportens design, genererad on-the-fly från senaste data.
    # Ingen fil sparas till reports/. UX-ändringar (CSS/JS i html_report.py) syns
    # direkt vid nästa sidladdning.
    ROOT = Path(__file__).parent
    import importlib
    import inspect
    import output.html_report as _hr
    # Streamlit Cloud kan re-köra huvudskriptet men behålla en GAMMAL cachad version
    # av importerade submoduler i sys.modules. Om den cachade generate() saknar
    # read_only (dvs. modulen är äldre än pushen) → ladda om från disk. Annars
    # kraschar hela Live-vyn med "unexpected keyword argument 'read_only'".
    if "read_only" not in inspect.signature(_hr.generate).parameters:
        _hr = importlib.reload(_hr)
    _gen_html = _hr.generate
    import tempfile

    def _load_json(name: str) -> dict:
        try:
            with open(ROOT / name, encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}

    # Prio-ändringar sparas server-side via den nativa panelen nedan (se _save_override).
    # Ingen token skickas till webbläsaren och ingen localStorage-brygga behövs längre.

    # Filter: items som inte hör hemma i journalist-vyn (workshops, interna admin, saknar tech-vinkel)
    _STRETCH_TECH_PATTERNS = (
        "ingen tydlig tech", "ingen tech-vinkel", "saknar tech-vinkel",
    )
    _INTERNAL_ADMIN_PATTERNS = (
        # Regeringsintern administration
        "lämnar in", "lämnar över", "lämnas över", "överlämnar",
        "överlämnade", "lämnade in", "överlämning av",
        "regeringen ger", "regeringen ger i uppdrag",
        "regeringsuppdrag", "regeringens skrivelse",
        "propositionens ankomst", "ministern besöker",
        "delbetänkande överlämnas", "utredningen presenterar",
        "utredningsdirektiv", "kommittédirektiv",
        "riksrevisionens rapport", "årsredovisning",
        # Möten och utfrågningar (interna proceduren)
        "exchange of views", "hearing on", "presentation of",
        "meeting of", "voting time", "committee meeting",
        "public hearing", "structured dialogue",
        # Konsultationer och remisser (inte substantiella beslut)
        "call for input", "call for evidence", "have your say",
        "public consultation", "targeted consultation",
    )

    def _should_exclude(item: dict) -> bool:
        """Filtrerar bort items som inte är intressanta nyhetsvinklar:
        1. Saknar tech-vinkel eller AI sa 'ingen tydlig tech-vinkel'
        2. Interna administrativa dokument (regeringsuppdrag, hearings, konsultationer)"""
        a = item.get("analysis") or {}
        tv = (a.get("tech_vinkel") or "").strip().lower()
        if not tv:
            return True
        if any(p in tv for p in _STRETCH_TECH_PATTERNS):
            return True
        title_low = (item.get("title") or "").lower()
        samm_low = (a.get("sammanfattning") or "").lower()
        haystack = f"{title_low} {samm_low}"
        if any(p in haystack for p in _INTERNAL_ADMIN_PATTERNS):
            return True
        return False

    _all_items = []
    _seen_urls: set[str] = set()
    for _day_items in _load_json(".agent_memory.json").values():
        for _it in _day_items:
            _u = _it.get("url", "")
            if _u and _u in _seen_urls:
                continue  # samma URL i flera dagar → visa bara en gång
            if _u:
                _seen_urls.add(_u)
            _all_items.append(_it)
    # Komplettera med cache-items som INTE finns i memory — så att analyserade
    # items som filtrerats bort vid körningen ändå visas på sajten i sina rätta
    # sektioner. De hamnar inte i Nytt idag eftersom den filtrerar på publiceringsdatum.
    for _url, _entry in _load_json(".agent_analysis_cache.json").items():
        if not _url or _url in _seen_urls:
            continue
        _all_items.append({
            "title": _entry.get("title", ""),
            "url": _url,
            "source": _entry.get("source", ""),
            "type": _entry.get("type", ""),
            "date": _entry.get("date", ""),
            "committee": _entry.get("committee", ""),
            "summary": _entry.get("summary", ""),
            "analysis": _entry.get("analysis", {}),
        })
    # Auto-filter: items utan tech-vinkel / interna procedurer (workshops, hearings,
    # konsultationer) hör inte hemma i journalist-vyn. MEN vi tar INTE bort dem ur
    # _all_items längre — panelen ska kunna hitta OCH styra ALLA ärenden. Vi markerar
    # bara vilka som auto-göms i iframen. Sätter du en prio manuellt vinner ditt val
    # över auto-filtret (invariant #4 — respektera overrides).
    _auto_excluded = {it.get("url", "") for it in _all_items if _should_exclude(it)}

    # ── Applicera manuella prio-overrides server-side ─────────────────────
    # Sanningen är .agent_overrides.json (som AI:n också läser). Vi speglar in den
    # i itemens relevans så iframen visar rätt färg/prio direkt — ingen localStorage,
    # ingen token i webbläsaren.
    if "live_overrides" not in st.session_state:
        st.session_state["live_overrides"] = _load_overrides_file()
    _ov = st.session_state["live_overrides"]
    for _it in _all_items:
        _u = _it.get("url", "")
        if _u in _ov and _ov[_u] in VALID_RELEVANS:
            _it.setdefault("analysis", {})["relevans"] = _ov[_u]

    # ── Vy-växel (TEST) ──────────────────────────────────────────────────
    # HTML-vyn (default) = dagens dashboard i iframe. Nativa kort = prio-dropdown
    # direkt på varje kort (ingen sökning). Så du kan jämföra och avgöra looken själv.
    _view_mode = st.radio(
        "Vy",
        ["🎨 HTML-vy (som idag)", "🧩 Nativa kort (test — prio direkt på kortet)"],
        horizontal=True,
        key="live_view_mode",
        label_visibility="collapsed",
    )
    _native = _view_mode.startswith("🧩")

    # ── Snabb-panel: ändra prioritet via sökning (sparar direkt server-side) ──
    _PRIOS = ["hög", "medel", "låg", "utesluten"]
    _PRIO_LABEL = {"hög": "Hög prioritet", "medel": "Medel", "låg": "Låg", "utesluten": "Utesluten"}
    _PRIO_RANK = {"hög": 0, "medel": 1, "låg": 2, "okänd": 3, "utesluten": 4}

    def _clean_t(it: dict) -> str:
        # Samma tvättade/svenska rubrik som visas på korten → sökningen i panelen
        # matchar det du faktiskt ser (inte den råa engelska titeln).
        try:
            return _hr._clean_title(it)
        except Exception:
            return it.get("title") or "Utan titel"

    _editable = [it for it in _all_items if it.get("url")]
    _editable.sort(key=lambda it: (_PRIO_RANK.get(it.get("analysis", {}).get("relevans", "okänd"), 3),
                                   _clean_t(it).lower()))
    _url_to_item = {it["url"]: it for it in _editable}

    def _opt_label(u: str) -> str:
        it = _url_to_item.get(u, {})
        rel = it.get("analysis", {}).get("relevans", "okänd")
        src = it.get("source", "")
        return f"{RELEVANS_EMOJI.get(rel, '⚪')} {_clean_t(it)[:90]}" + (f" · {src}" if src else "")

    with st.container(border=True):
        st.markdown("#### ✎ Ändra prioritet på ett ärende")
        if _native:
            st.caption("Nativ vy nedan — ändra prio direkt på varje kort. "
                       "Den här sökrutan är bara en genväg om du vill hoppa till ett visst ärende.")
        else:
            st.caption("1️⃣ Sök ärendet i fältet nedan  ·  2️⃣ välj ny prioritet till höger. "
                       "Sparas direkt. (Dropdownarna på korten längre ner är bara färg-etiketter.)")
        _pc1, _pc2 = st.columns([3, 1])
        with _pc1:
            _sel_url = st.selectbox(
                "Välj ärende",
                options=[it["url"] for it in _editable],
                index=None,
                format_func=_opt_label,
                placeholder="Sök ärende (skriv del av titeln)…",
                key="qe_item",
                label_visibility="collapsed",
            )
        with _pc2:
            if _sel_url:
                _cur = _url_to_item[_sel_url].get("analysis", {}).get("relevans", "okänd")
                _cur = _cur if _cur in _PRIOS else "medel"
                # Nollställ prio-väljaren när man byter ärende (så inget sparas av misstag)
                if st.session_state.get("qe_prev_url") != _sel_url:
                    st.session_state["qe_prio"] = _cur
                    st.session_state["qe_prev_url"] = _sel_url
                _new = st.selectbox(
                    "Ny prioritet",
                    options=_PRIOS,
                    format_func=lambda p: f"{RELEVANS_EMOJI.get(p, '⚪')} {_PRIO_LABEL.get(p, p)}",
                    key="qe_prio",
                    label_visibility="collapsed",
                )
                if _new != _cur:
                    _ok, _msg = _save_override(_sel_url, _new)
                    st.toast(f"✓ Sparat: {_PRIO_LABEL[_new]}" if _ok else f"⚠ {_msg}",
                             icon="💾" if _ok else "⚠️")
                    st.rerun()
            else:
                st.caption("Välj ett ärende →")
        if not _get_pat():
            st.caption("⚠ GITHUB_PAT saknas — ändringar sparas lokalt men pushas inte till repot.")

    # ── Vilka items visas i iframen? ─────────────────────────────────────
    # - "utesluten" (manuellt) → alltid gömd
    # - manuell prio satt (hög/medel/låg) → alltid visad (ditt val slår auto-filtret)
    # - ingen manuell prio → visas om auto-filtret inte gömmer den
    _iframe_items = []
    for _it in _all_items:
        _u = _it.get("url", "")
        _rel = _it.get("analysis", {}).get("relevans", "okänd")  # override redan applicerad ovan
        if _rel == "utesluten":
            continue
        if (_u in _ov) or (_u not in _auto_excluded):
            _iframe_items.append(_it)

    # Bygg important_dates: kombinera .agent_dates.json (bara memory-items har landat där)
    # med viktiga_datum från de items som faktiskt visas (inkl cache-items som annars saknar
    # sina framtida datum).
    from datetime import datetime as _dt
    _important_dates = _load_json(".agent_dates.json") or {}
    _seen_date_entries: set[tuple[str, str, str]] = set()
    for _d_iso, _entries in _important_dates.items():
        for _e in _entries:
            _seen_date_entries.add((_d_iso, _e.get("title", ""), _e.get("beskrivning", "")))
    for _it in _iframe_items:
        _analysis = _it.get("analysis") or {}
        for _vd in (_analysis.get("viktiga_datum") or []):
            _datum = _vd.get("datum", "")
            _beskr = _vd.get("beskrivning", "")
            if not _datum or not _beskr:
                continue
            try:
                _dt.strptime(_datum, "%Y-%m-%d")
            except (ValueError, TypeError):
                continue
            _entry = {
                "beskrivning": _beskr,
                "title": _it.get("title", ""),
                "url": _it.get("url", ""),
                "arende": _analysis.get("arende", "") or "",
            }
            _key = (_datum, _entry["title"], _beskr)
            if _key in _seen_date_entries:
                continue
            _seen_date_entries.add(_key)
            _important_dates.setdefault(_datum, []).append(_entry)

    # _iframe_items byggdes ovan (respekterar 'utesluten' + manuella overrides över auto-filtret).
    if _native:
        _render_native_live(_iframe_items)
    else:
        try:
            with tempfile.NamedTemporaryFile(mode="w", suffix=".html", delete=False, encoding="utf-8") as _f:
                _tmp_path = _f.name
            # github_pat="" → ingen token i webbläsaren; sparning sker server-side via panelen.
            # read_only=True → iframens egna dropdowns/kopiera-bar stängs av (ett ställe att ändra på).
            _gen_html(_iframe_items, output_path=_tmp_path, important_dates=_important_dates,
                      include_header=False,
                      github_pat="",
                      github_repo="",
                      read_only=True)
            with open(_tmp_path, encoding="utf-8") as _f:
                _live_html = _f.read()
            os.unlink(_tmp_path)
            st.components.v1.html(_live_html, height=2200, scrolling=True)
        except Exception as _e:
            st.error(f"Kunde inte bygga live-vyn: {type(_e).__name__}: {_e}")


def _render_tab(label: str, items: list[dict]) -> None:
    st.markdown(f"### {label} — {len(items)} ärenden, nyaste först")
    if not items:
        st.info("Inga ärenden här just nu.")
        return
    for item in items:
        _render_item(item)


with tab_riksdag:
    _render_tab("Riksdagen", riksdagen_items)
with tab_reg:
    _render_tab("Regeringen", regeringen_items)
with tab_ep:
    _render_tab("EU-parlamentet", ep_items)
with tab_ek:
    _render_tab("EU-kommissionen", ek_items)
with tab_byraer:
    _render_tab("EU-byråer", agency_items)
with tab_media:
    st.caption("⚠ Sekundärkällor — Politico EU och EDRi rapporterar EU-policy-strider som "
               "officiella institutionsfeeds ofta missar (t.ex. Chat Control/CSAM). "
               "Använd som signal för att gräva vidare i primärkällor.")
    _render_tab("Tech-policy-media", media_items)
