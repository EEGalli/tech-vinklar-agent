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

RELEVANS_EMOJI = {"hög": "🔴", "medel": "🟡", "låg": "🟢", "okänd": "⚪"}

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
    return st.secrets.get("GITHUB_PAT", "") if hasattr(st, "secrets") else ""


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
    from output.html_report import generate as _gen_html
    import tempfile
    import base64 as _b64

    def _load_json(name: str) -> dict:
        try:
            with open(ROOT / name, encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}

    # ── Auto-sync av prio-ändringar: localStorage → GitHub ────
    # HTML-rapporten sparar ändringar i webbläsarens localStorage under
    # 'tv_relevans_overrides_v1'. Vid varje sidladdning läses den nyckeln + synkas
    # till .agent_overrides.json i repo:t om något ändrats. Så AI:n ser dem.
    _VALID_RELEVANS = {"hög", "medel", "låg", "utesluten"}

    def _is_safe_url(u: str) -> bool:
        return (isinstance(u, str) and u.strip().startswith(("http://", "https://"))
                and len(u) < 2000)

    def _sync_overrides_to_github(new_data: dict, pat: str) -> tuple[bool, str]:
        """PAT skickas ENDAST i Authorization-header. Felmeddelanden avslöjar aldrig
        token-innehåll — bara HTTP-status + generisk hint."""
        if not pat:
            return False, "GITHUB_PAT saknas"
        # Validera + slå ihop med befintlig repo-fil
        current = _load_json(".agent_overrides.json") or {}
        merged = dict(current)
        for url, val in new_data.items():
            if _is_safe_url(url) and val in _VALID_RELEVANS:
                merged[url] = val
        if merged == current:
            return True, "ingen ändring"
        new_content = json.dumps(merged, ensure_ascii=False, indent=2) + "\n"
        api = f"https://api.github.com/repos/{GITHUB_REPO}/contents/.agent_overrides.json"
        headers = {"Authorization": f"Bearer {pat}", "Accept": "application/vnd.github+json"}
        try:
            r = requests.get(api, headers=headers, timeout=10)
        except requests.RequestException as e:
            return False, f"Nätverksfel: {type(e).__name__}"
        sha = r.json().get("sha") if r.status_code == 200 else None
        if r.status_code not in (200, 404):
            return False, f"GET misslyckades (HTTP {r.status_code})"
        payload = {
            "message": f"Auto-sync prio-ändringar ({len(new_data)} från browser)",
            "content": _b64.b64encode(new_content.encode("utf-8")).decode("ascii"),
        }
        if sha:
            payload["sha"] = sha
        try:
            r = requests.put(api, headers=headers, json=payload, timeout=15)
        except requests.RequestException as e:
            return False, f"Nätverksfel vid PUT: {type(e).__name__}"
        if r.status_code in (200, 201):
            # Uppdatera lokal kopia så nästa sidladdning inte pushar igen
            try:
                (ROOT / ".agent_overrides.json").write_text(new_content, encoding="utf-8")
            except Exception:
                pass
            return True, f"synkade {len(merged)} val"
        return False, f"PUT misslyckades (HTTP {r.status_code})"

    # Läs localStorage + visa knapp för manuell synk. Ingen "auto-sync i bakgrunden"
    # som failar tyst — hon trycker på knappen och ser resultatet direkt.
    _local_overrides = {}
    _lstorage_err = ""
    try:
        from streamlit_local_storage import LocalStorage
        _lstorage = LocalStorage()
        _local_overrides_raw = _lstorage.getItem("tv_relevans_overrides_v1")
        if _local_overrides_raw:
            try:
                _local_overrides = json.loads(_local_overrides_raw)
            except Exception:
                _lstorage_err = "trasig JSON i localStorage"
    except ImportError:
        _lstorage_err = "streamlit-local-storage saknas"
    except Exception as _e:
        _lstorage_err = f"localStorage-fel: {type(_e).__name__}"

    _committed = _load_json(".agent_overrides.json") or {}
    _diff_count = sum(
        1 for k, v in _local_overrides.items()
        if _is_safe_url(k) and v in _VALID_RELEVANS and _committed.get(k) != v
    )

    # Statusrad + spara-knapp — bara synlig om det finns något att synka
    if _lstorage_err:
        st.error(f"⚠ Kan inte läsa dina prio-ändringar: {_lstorage_err}")
    elif _diff_count > 0:
        _s1, _s2 = st.columns([3, 1])
        with _s1:
            st.info(f"✏️ {_diff_count} osparade prio-ändringar i webbläsaren")
        with _s2:
            if st.button("💾 Spara till repo", type="primary", key="save_prios",
                         use_container_width=True):
                _pat = _get_pat()
                if not _pat:
                    st.error("GITHUB_PAT saknas i Streamlit secrets — kan inte skriva till repo")
                else:
                    _ok, _msg = _sync_overrides_to_github(_local_overrides, _pat)
                    if _ok:
                        st.success(f"✓ {_msg}")
                        st.rerun()
                    else:
                        st.error(f"Sparfel: {_msg}")

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
    # Filtrera bort items utan tech-vinkel och interna admin-dokument
    _before = len(_all_items)
    _all_items = [it for it in _all_items if not _should_exclude(it)]
    _filtered = _before - len(_all_items)

    # Bygg important_dates: kombinera .agent_dates.json (bara memory-items har landat där)
    # med viktiga_datum från alla items (inkl cache-items som annars saknar sina framtida datum).
    from datetime import datetime as _dt
    _important_dates = _load_json(".agent_dates.json") or {}
    _seen_date_entries: set[tuple[str, str, str]] = set()
    for _d_iso, _entries in _important_dates.items():
        for _e in _entries:
            _seen_date_entries.add((_d_iso, _e.get("title", ""), _e.get("beskrivning", "")))
    for _it in _all_items:
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

    try:
        with tempfile.NamedTemporaryFile(mode="w", suffix=".html", delete=False, encoding="utf-8") as _f:
            _tmp_path = _f.name
        _gen_html(_all_items, output_path=_tmp_path, important_dates=_important_dates,
                  include_header=False)
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
