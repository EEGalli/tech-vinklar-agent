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


# ── Sidebar ──────────────────────────────────────────────
st.sidebar.title("🔍 Tech Vinklar")
st.sidebar.markdown("EU & Riksdagen — daglig tech-bevakning")

reports = _list_reports()

if not reports:
    st.warning("Inga rapporter hittades. Kör `python3 main.py` lokalt först.")
    st.stop()

st.sidebar.markdown("### Välj rapport")
labels = [_format_label(p) for p in reports]
selected_idx = st.sidebar.selectbox(
    "Datum & tid",
    options=range(len(reports)),
    format_func=lambda i: labels[i],
    index=0,
    label_visibility="collapsed",
)

selected = reports[selected_idx]

st.sidebar.markdown("---")
st.sidebar.markdown(
    f"**{len(reports)}** rapporter sparade  \n"
    f"Senaste: **{labels[0]}**"
)

st.sidebar.markdown("---")
st.sidebar.markdown("### Kör ny analys")


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


trigger = st.sidebar.button("🔄 Starta körning", use_container_width=True)
refresh = st.sidebar.button("↻ Uppdatera status", use_container_width=True)

if trigger:
    pat = _get_pat()
    if not pat:
        st.sidebar.error("GITHUB_PAT saknas i Streamlit secrets.")
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
                st.sidebar.success("✓ Körning startad!")
            else:
                st.sidebar.error(f"Fel ({r.status_code}): {r.text[:200]}")
        except Exception as e:
            st.sidebar.error(f"Anslutningsfel: {e}")

# Visa status av senaste körningen (uppdateras vid omladdning eller ↻-klick)
pat = _get_pat()
dispatch_at = st.session_state.get("dispatch_at", "")  # ISO-tidpunkt eller ""
if pat:
    run = _latest_run_status(pat)
    if run:
        status = run.get("status", "")          # queued / in_progress / completed
        conclusion = run.get("conclusion", "")  # success / failure / cancelled / null
        run_created = run.get("created_at", "")  # full ISO med Z
        started_label = run_created[:16].replace("T", " ")
        run_url = run.get("html_url", "")

        # Om vi nyligen klickat: kolla om nuvarande körning är NYARE än vårt klick
        is_new_run = bool(dispatch_at and run_created and run_created >= dispatch_at)
        waiting_for_new = bool(dispatch_at and not is_new_run)

        if waiting_for_new:
            st.sidebar.info(
                "⏳ Körning startas… GitHub registrerar den om ~10 sek.\n\n"
                "Tryck ↻ om en stund för uppdaterad status."
            )
        elif status == "completed" and conclusion == "success":
            st.sidebar.success(
                f"✓ Senaste körning klar ({started_label})\n\n"
                "Ladda om sidan för att se rapporten."
            )
        elif status == "completed":
            st.sidebar.error(
                f"✗ Senaste körning misslyckades: {conclusion}\n\n[Se loggar]({run_url})"
            )
        elif status in ("queued", "in_progress", "waiting", "requested"):
            # Beräkna körtid + visa varje steg
            from datetime import timezone
            try:
                started = datetime.fromisoformat(run_created.replace("Z", "+00:00"))
                elapsed = datetime.now(timezone.utc) - started
                mins, secs = divmod(int(elapsed.total_seconds()), 60)
                duration = f"{mins}m {secs}s"
            except Exception:
                duration = "?"

            st.sidebar.info(f"⏳ Körning pågår ({status}) — körtid {duration}")

            steps = _get_run_steps(pat, run["id"])
            if steps:
                st.sidebar.markdown("**Steg:**")
                lines = []
                for s in steps:
                    name = s.get("name", "")
                    if name in ("Set up job", "Post job cleanup", "Complete job"):
                        continue  # GitHub-internt brus
                    lines.append(f"{_step_emoji(s)} {name}")
                st.sidebar.markdown("\n\n".join(lines))
            st.sidebar.caption(f"Startad {started_label}. Tryck ↻ för att uppdatera.")
        else:
            st.sidebar.caption(f"Senaste: {status} ({started_label})")

# ── Sökruta ───────────────────────────────────────────────
st.sidebar.markdown("---")
search_query = st.sidebar.text_input(
    "🔎 Sök",
    placeholder="t.ex. AI, NIS2, biometri",
    help="Söker i titel, sammanfattning, tech-vinkel och varför-viktigt",
)

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
    st.sidebar.caption(f"🔎 {total} träffar för \"{search_query}\"")

(tab_live, tab_dashboard, tab_riksdag, tab_reg, tab_myn,
 tab_ep, tab_ek, tab_byraer, tab_media) = st.tabs([
    "🔴 Live",
    "📊 Arkiv (HTML)",
    f"🇸🇪 Riksdagen ({len(riksdagen_items)})",
    f"🏛️ Regeringen ({len(regeringen_items)})",
    f"🏤 SE-myndigheter ({len(se_myndigheter_items)})",
    f"🇪🇺 EU-parlamentet ({len(ep_items)})",
    f"🇪🇺 EU-kommissionen ({len(ek_items)})",
    f"🏢 EU-byråer ({len(agency_items)})",
    f"📰 Tech-media ({len(media_items)})",
])

with tab_live:
    # Live-vyn regenererar HTML-rapporten från senaste data varje gång fliken
    # laddas — samma utseende som Arkiv-flikens rapporter, men alltid färskt
    # och med alla nyaste prio-ändringar från .agent_overrides.json.
    ROOT = Path(__file__).parent
    from output.html_report import generate as _gen_html
    import tempfile

    def _load_json(name: str) -> dict:
        try:
            with open(ROOT / name, encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}

    _all_items = []
    for _day_items in _load_json(".agent_memory.json").values():
        _all_items.extend(_day_items)

    try:
        with tempfile.NamedTemporaryFile(mode="w", suffix=".html", delete=False, encoding="utf-8") as _f:
            _tmp_path = _f.name
        _gen_html(_all_items, output_path=_tmp_path)
        with open(_tmp_path, encoding="utf-8") as _f:
            _live_html = _f.read()
        os.unlink(_tmp_path)
        st.components.v1.html(_live_html, height=2200, scrolling=True)
    except Exception as _e:
        st.error(f"Kunde inte bygga live-vyn: {type(_e).__name__}")
        st.exception(_e)

with tab_dashboard:
    with open(selected, encoding="utf-8") as f:
        html = f.read()
    st.components.v1.html(html, height=2200, scrolling=True)


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
