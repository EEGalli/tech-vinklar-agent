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
    "EU-parlamentet": "EU",
    "EU-kommissionen": "EU",
    "ENISA": "EU",
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
    """Formaterar 'digest_20260427_0808.html' → '27 april 2026, 08:08'."""
    name = p.stem.replace("digest_", "").replace("_rebuild", "").replace("_nytt", "")
    parts = name.split("_")
    if len(parts) >= 2:
        try:
            dt = datetime.strptime(f"{parts[0]}_{parts[1][:4]}", "%Y%m%d_%H%M")
            return dt.strftime("%-d %b %Y, %H:%M")
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
    """Renderar ett item som ett kompakt expanderbart kort."""
    a = item.get("analysis", {})
    title = item.get("title", "Utan titel")
    relevans = a.get("relevans", "okänd")
    emoji = RELEVANS_EMOJI.get(relevans, "⚪")
    date_str = (item.get("date") or "")[:10]
    source = item.get("source", "")
    item_type = item.get("type", "")

    header = f"{emoji} **{title}**"
    with st.expander(header, expanded=False):
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
        url = item.get("url")
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
if st.sidebar.button("🔄 Starta körning i molnet", use_container_width=True):
    pat = st.secrets.get("GITHUB_PAT", "") if hasattr(st, "secrets") else ""
    if not pat:
        st.sidebar.error(
            "GITHUB_PAT saknas i Streamlit secrets. "
            "Lägg till en Personal Access Token med `workflow`-scope."
        )
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
                st.sidebar.success(
                    "✓ Körning startad! Tar ~2–5 min. "
                    f"Följ status: https://github.com/{GITHUB_REPO}/actions"
                )
            else:
                st.sidebar.error(f"Fel ({r.status_code}): {r.text[:200]}")
        except Exception as e:
            st.sidebar.error(f"Anslutningsfel: {e}")

# ── Huvudvy: tabbar ──────────────────────────────────────
all_items = _load_all_items()
all_items.sort(key=lambda i: _date_int(i.get("date", "")), reverse=True)

def _filter_by_tab(tab: str) -> list[dict]:
    return [i for i in all_items if SOURCE_TO_TAB.get(i.get("source", "")) == tab]

riksdagen_items = _filter_by_tab("Riksdagen")
regeringen_items = _filter_by_tab("Regeringen")
eu_items = _filter_by_tab("EU")

tab_dashboard, tab_riksdag, tab_reg, tab_eu = st.tabs([
    "📊 Dashboard",
    f"🇸🇪 Riksdagen ({len(riksdagen_items)})",
    f"🏛️ Regeringen ({len(regeringen_items)})",
    f"🇪🇺 EU ({len(eu_items)})",
])

with tab_dashboard:
    with open(selected, encoding="utf-8") as f:
        html = f.read()
    st.components.v1.html(html, height=2200, scrolling=True)

with tab_riksdag:
    st.markdown(f"### Riksdagen — {len(riksdagen_items)} ärenden, nyaste först")
    if not riksdagen_items:
        st.info("Inga ärenden från Riksdagen i minnet.")
    for item in riksdagen_items:
        _render_item(item)

with tab_reg:
    st.markdown(f"### Regeringen — {len(regeringen_items)} ärenden, nyaste först")
    if not regeringen_items:
        st.info("Inga ärenden från Regeringen i minnet.")
    for item in regeringen_items:
        _render_item(item)

with tab_eu:
    st.markdown(f"### EU (parlamentet, kommissionen, ENISA) — {len(eu_items)} ärenden, nyaste först")
    if not eu_items:
        st.info("Inga EU-ärenden i minnet.")
    for item in eu_items:
        _render_item(item)
