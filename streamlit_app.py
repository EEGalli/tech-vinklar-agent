"""
Streamlit-app som visar de senaste tech-vinkel-rapporterna.

Lokal körning:  streamlit run streamlit_app.py
Streamlit Cloud: pekar på samma repo, läser reports/ direkt.
"""
import os
from pathlib import Path
from datetime import datetime

import requests
import streamlit as st

GITHUB_REPO = "EEGalli/tech-vinklar-agent"
WORKFLOW_FILE = "run-agent.yml"

REPORTS_DIR = Path(__file__).parent / "reports"

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

# ── Huvudvy ──────────────────────────────────────────────
with open(selected, encoding="utf-8") as f:
    html = f.read()

# Visa rapporten via en iframe-liknande komponent
st.components.v1.html(html, height=2200, scrolling=True)
