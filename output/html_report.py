"""
Genererar en snygg HTML-rapport med kalendervy av analyserade ärenden.
Öppnas i webbläsaren, kopieras enkelt in i Google Docs.
"""
import calendar
from collections import defaultdict
from datetime import datetime, date, timedelta
from typing import Optional


RELEVANCE_EMOJI = {"hög": "🔴", "medel": "🟡", "låg": "🟢"}
RELEVANCE_LABEL = {"hög": "Hög prioritet", "medel": "Medel", "låg": "Låg"}
RELEVANCE_COLOR = {"hög": "#c0392b", "medel": "#d68910", "låg": "#27ae60"}
RELEVANCE_DOT  = {"hög": "#e74c3c", "medel": "#f39c12", "låg": "#2ecc71"}

SWEDISH_MONTHS = [
    "", "januari", "februari", "mars", "april", "maj", "juni",
    "juli", "augusti", "september", "oktober", "november", "december"
]
SWEDISH_DAYS_SHORT = ["Mån", "Tis", "Ons", "Tor", "Fre", "Lör", "Sön"]


def _parse_date(date_str: str) -> Optional[date]:
    for fmt in ("%Y-%m-%d", "%Y-%m-%dT%H:%M:%S", "%a, %d %b %Y"):
        try:
            return datetime.strptime(date_str[:len(fmt) + 2], fmt).date()
        except Exception:
            continue
    return None


def _swedish_date(d: date) -> str:
    return f"{d.day} {SWEDISH_MONTHS[d.month]}"


def _date_sort_key(date_str: str) -> int:
    """ISO-datum som heltal (YYYYMMDD) för sortering. Returnerar 0 om tomt/ogiltigt."""
    if not date_str:
        return 0
    s = date_str[:10].replace("-", "")
    try:
        return int(s)
    except ValueError:
        return 0


def _build_calendar_section(items: list[dict], important_dates: dict = None) -> str:
    """Bygger idag / denna vecka / månadskalender med klickbara datum."""
    import json as _json
    today = date.today()

    # Indexera items på datum
    by_date: dict[date, list] = defaultdict(list)
    for item in items:
        d = _parse_date(item.get("date", ""))
        if d:
            by_date[d].append(item)

    # Bygg JSON-data för JavaScript (datum → lista med ärenden)
    js_data = {}
    for d, day_items in by_date.items():
        js_data[d.isoformat()] = [
            {
                "title": i.get("title", ""),
                "url": i.get("url", ""),
                "source": i.get("source", ""),
                "committee": i.get("committee", ""),
                "relevans": i.get("analysis", {}).get("relevans", "okänd"),
                "tech_vinkel": i.get("analysis", {}).get("tech_vinkel", ""),
                "varfor": i.get("analysis", {}).get("varfor_viktigt", ""),
                "eu_koppling": i.get("analysis", {}).get("eu_koppling") or "",
            }
            for i in day_items
        ]
    # Bygg en lookup från källdokumentets titel → dess analys.
    # Inkludera både dagens items OCH hela minnet (items kan finnas historiskt
    # men deras viktiga_datum pekar framåt — då måste vi ändå kunna hitta
    # deras analys för att visa sammanfattning/tech-vinkel på datum-eventet).
    title_to_analysis: dict[str, dict] = {}
    for item in items:
        t = item.get("title", "")
        if t:
            title_to_analysis[t] = item.get("analysis", {})
    try:
        import memory as _mem
        raw_mem = _mem._load_raw()
        # Iterera från NYASTE dag först och prioritera analyser med sammanfattning
        for day in sorted(raw_mem.keys(), reverse=True):
            for item in raw_mem[day]:
                t = item.get("title", "")
                a = item.get("analysis", {})
                if not (t and a):
                    continue
                existing = title_to_analysis.get(t)
                has_samm = bool(a.get("sammanfattning"))
                existing_has_samm = bool(existing and existing.get("sammanfattning"))
                # Skriv över om vi inte hade nån, ELLER om vi hade en utan samm men nu hittat med samm
                if existing is None or (not existing_has_samm and has_samm):
                    title_to_analysis[t] = a
    except Exception:
        pass

    # Lägg till viktiga datum från AI-extraktion
    for d_iso, entries in (important_dates or {}).items():
        try:
            d = date.fromisoformat(d_iso)
        except ValueError:
            continue
        if d not in by_date:
            by_date[d] = []
        existing_beskrivningar = {i.get("title","") for i in by_date[d]}
        existing_urls = {i.get("url","") for i in by_date[d] if i.get("url")}
        for e in entries:
            beskrivning = e.get("beskrivning", "")
            src_url = e.get("url", "")
            # Dedupera mot allt som redan finns på datumet:
            # 1. Samma beskrivning (gamla dedup)
            # 2. Samma URL — synthetic item är en duplicering av källdokumentet
            if beskrivning in existing_beskrivningar:
                continue
            if src_url and src_url in existing_urls:
                continue
            # Hämta sammanfattning + tech-vinkel från källdokumentet
            src_title = e.get("title", "")
            src_analysis = title_to_analysis.get(src_title, {})
            by_date[d].append({
                "title": beskrivning,
                "url": e.get("url", ""),
                "source": e.get("arende", "") or "Viktigt datum",
                "committee": "",
                "type": "Viktigt datum",
                "date": d_iso,
                "analysis": {
                    "relevans": src_analysis.get("relevans", "hög"),
                    "sammanfattning": src_analysis.get("sammanfattning", ""),
                    "tech_vinkel": src_analysis.get("tech_vinkel", ""),
                    "varfor_viktigt": src_analysis.get("varfor_viktigt", ""),
                    "eu_koppling": src_analysis.get("eu_koppling") or "",
                },
            })

    # Bygg om js_data efter att viktiga datum lagts till
    js_data = {}
    for d, day_items in by_date.items():
        js_data[d.isoformat()] = [
            {
                "title": i.get("title", ""),
                "url": i.get("url", ""),
                "source": i.get("source", ""),
                "committee": i.get("committee", ""),
                "relevans": i.get("analysis", {}).get("relevans", "okänd"),
                "sammanfattning": i.get("analysis", {}).get("sammanfattning", ""),
                "tech_vinkel": i.get("analysis", {}).get("tech_vinkel", ""),
                "varfor": i.get("analysis", {}).get("varfor_viktigt", ""),
                "eu_koppling": i.get("analysis", {}).get("eu_koppling") or "",
            }
            for i in day_items
        ]

    js_data_str = _json.dumps(js_data, ensure_ascii=False)

    # ── Idag ──────────────────────────────────────────────
    today_items = by_date.get(today, [])
    if today_items:
        today_html = "".join(_mini_card(i) for i in today_items)
    else:
        today_html = '<p class="cal-empty">Inga schemalagda ärenden idag.</p>'

    # ── Denna vecka ────────────────────────────────────────
    week_days_html = ""
    for offset in range(1, 8):
        d = today + timedelta(days=offset)
        day_items = by_date.get(d, [])
        if day_items:
            rows = "".join(_mini_card(i) for i in day_items)
            week_days_html += f"""
            <div class="week-day">
              <div class="week-day-label">{SWEDISH_DAYS_SHORT[d.weekday()]} {_swedish_date(d)}</div>
              {rows}
            </div>"""
    if not week_days_html:
        week_days_html = '<p class="cal-empty">Inga schemalagda ärenden denna vecka.</p>'

    month_cal_html = """
    <div class="cal-nav">
      <button class="cal-nav-btn" onclick="prevMonth()" id="cal-prev">◀</button>
      <span id="cal-month-title" style="font-weight:700;font-size:0.9rem"></span>
      <button class="cal-nav-btn" onclick="nextMonth()" id="cal-next">▶</button>
    </div>
    <table class="month-cal" id="cal-table">
      <thead><tr><th>Mån</th><th>Tis</th><th>Ons</th><th>Tor</th><th>Fre</th><th>Lör</th><th>Sön</th></tr></thead>
      <tbody id="cal-body"></tbody>
    </table>
    <div class="cal-legend">
      <span><span class="dot" style="background:#e74c3c"></span> Hög</span>
      <span><span class="dot" style="background:#f39c12"></span> Medel</span>
      <span><span class="dot" style="background:#2ecc71"></span> Låg</span>
    </div>"""

    # Beräkna max framåt baserat på CAL_DATA + minst 13 månader
    today_iso = today.isoformat()

    # JavaScript + sidopanel
    js_and_panel = f"""
    <!-- Datum-panel -->
    <div id="day-panel" class="day-panel" onclick="if(event.target===this)closePanel()">
      <div class="day-panel-inner">
        <div class="day-panel-header">
          <span id="day-panel-title"></span>
          <button class="close-btn" onclick="closePanel()">✕</button>
        </div>
        <div id="day-panel-body"></div>
      </div>
    </div>

    <script>
    const CAL_DATA = {js_data_str};
    const RELEVANCE_COLOR = {{"hög":"#c0392b","medel":"#d68910","låg":"#27ae60","okänd":"#888"}};
    const RELEVANCE_EMOJI = {{"hög":"🔴","medel":"🟡","låg":"🟢","okänd":"⚪"}};
    const MONTHS_SV = ["","januari","februari","mars","april","maj","juni","juli","augusti","september","oktober","november","december"];

    function formatDate(iso) {{
      const [y,m,d] = iso.split("-").map(Number);
      return d + " " + MONTHS_SV[m] + " " + y;
    }}

    function showDay(dateIso) {{
      const items = CAL_DATA[dateIso];
      if (!items || items.length === 0) return;
      document.getElementById("day-panel-title").textContent = "📅 " + formatDate(dateIso);
      const body = document.getElementById("day-panel-body");
      body.innerHTML = items.map(item => {{
        const color = RELEVANCE_COLOR[item.relevans] || "#888";
        const emoji = RELEVANCE_EMOJI[item.relevans] || "⚪";
        const link = item.url ? `<a href="${{item.url}}" target="_blank" class="panel-read-more">Läs originaldokumentet →</a>` : "";
        const eu = item.eu_koppling ? `<p class="panel-eu">🇪🇺 ${{item.eu_koppling}}</p>` : "";
        const samm = item.sammanfattning ? `<p class="panel-samm"><strong>Vad handlar det om?</strong> ${{item.sammanfattning}}</p>` : "";
        const vinkel = item.tech_vinkel ? `<p class="panel-vinkel"><strong>Tech-vinkel:</strong> ${{item.tech_vinkel}}</p>` : "";
        const varfor = item.varfor ? `<p class="panel-varfor"><strong>Varför viktigt:</strong> ${{item.varfor}}</p>` : "";
        const meta = [item.source, item.committee].filter(Boolean).join(" · ");
        return `
          <div class="panel-card" style="border-left:4px solid ${{color}}">
            <div class="panel-badge" style="background:${{color}}">${{emoji}} ${{item.relevans}}</div>
            <h4 class="panel-title">${{item.title}}</h4>
            <p class="panel-meta">${{meta}}</p>
            ${{samm}}${{vinkel}}${{varfor}}${{eu}}
            ${{link}}
          </div>`;
      }}).join("");
      document.getElementById("day-panel").classList.add("open");
    }}

    function closePanel() {{
      document.getElementById("day-panel").classList.remove("open");
    }}

    document.addEventListener("keydown", e => {{ if (e.key === "Escape") closePanel(); }});

    // ── Expandera mini-kort: visa full info i sidopanelen ──
    function expandMini(el) {{
      const raw = el.getAttribute("data-full");
      if (!raw) return;
      let d;
      try {{ d = JSON.parse(raw); }} catch (e) {{ return; }}
      document.getElementById("day-panel-title").textContent = d.title;
      const body = document.getElementById("day-panel-body");
      const meta = d.meta ? `<p class="panel-meta">${{d.meta}}</p>` : "";
      const samm = d.sammanfattning ? `<p class="panel-samm"><strong>Vad handlar det om?</strong> ${{d.sammanfattning}}</p>` : "";
      const vinkel = d.tech_vinkel ? `<p class="panel-vinkel"><strong>Tech-vinkel:</strong> ${{d.tech_vinkel}}</p>` : "";
      const varfor = d.varfor ? `<p class="panel-varfor"><strong>Varför viktigt:</strong> ${{d.varfor}}</p>` : "";
      const eu = d.eu_koppling ? `<p class="panel-eu">🇪🇺 ${{d.eu_koppling}}</p>` : "";
      const link = d.url ? `<a href="${{d.url}}" target="_blank" class="panel-read-more">Läs originaldokumentet →</a>` : "";
      body.innerHTML = `<div class="panel-card">${{meta}}${{samm}}${{vinkel}}${{varfor}}${{eu}}${{link}}</div>`;
      document.getElementById("day-panel").classList.add("open");
    }}

    // ── Navigerbar månadskalender ──────────────────────────
    const TODAY_ISO = "{today_iso}";
    const TODAY = new Date(TODAY_ISO + "T00:00:00");
    const MONTHS_SV_CAL = ["Januari","Februari","Mars","April","Maj","Juni",
                            "Juli","Augusti","September","Oktober","November","December"];
    const DAYS_SV = ["Mån","Tis","Ons","Tor","Fre","Lör","Sön"];
    const REL_DOT = {{"hög":"#e74c3c","medel":"#f39c12","låg":"#2ecc71","okänd":"#ccc"}};

    // Beräkna max månad (minst 13 månader fram, eller längsta datum i CAL_DATA)
    let maxYear = TODAY.getFullYear(), maxMonth = TODAY.getMonth() + 14;
    if (maxMonth > 12) {{ maxYear += Math.floor((maxMonth-1)/12); maxMonth = ((maxMonth-1)%12)+1; }}
    for (const iso of Object.keys(CAL_DATA)) {{
      const [y,m] = iso.split("-").map(Number);
      if (y > maxYear || (y === maxYear && m > maxMonth)) {{ maxYear = y; maxMonth = m; }}
    }}

    let calYear = TODAY.getFullYear();
    let calMonth = TODAY.getMonth() + 1; // 1-indexed

    function renderCalendar(year, month) {{
      document.getElementById("cal-month-title").textContent =
        MONTHS_SV_CAL[month-1] + " " + year;

      // Prev-knapp: aldrig bakåt förbi nuvarande månad
      const atMin = (year === TODAY.getFullYear() && month === TODAY.getMonth()+1);
      document.getElementById("cal-prev").disabled = atMin;
      document.getElementById("cal-prev").style.opacity = atMin ? "0.3" : "1";

      // Next-knapp: aldrig framåt förbi max
      const atMax = (year === maxYear && month === maxMonth);
      document.getElementById("cal-next").disabled = atMax;
      document.getElementById("cal-next").style.opacity = atMax ? "0.3" : "1";

      // Bygg rader
      const firstDay = new Date(year, month-1, 1).getDay(); // 0=sön
      const daysInMonth = new Date(year, month, 0).getDate();
      // Justera: måndag = 0
      const startOffset = (firstDay + 6) % 7;

      let html = "<tr>";
      for (let i = 0; i < startOffset; i++) html += '<td class="cal-empty-cell"></td>';

      let col = startOffset;
      for (let day = 1; day <= daysInMonth; day++) {{
        const iso = year + "-" + String(month).padStart(2,"0") + "-" + String(day).padStart(2,"0");
        const items = CAL_DATA[iso] || [];
        const isToday = iso === TODAY_ISO ? "today" : "";
        const hasItems = items.length ? "has-items" : "";
        const click = items.length ? `onclick="showDay('${{iso}}')"` : "";

        let dots = "";
        const seen = new Set();
        for (const it of items.slice(0,3)) {{
          const r = it.relevans || "okänd";
          if (!seen.has(r)) {{ seen.add(r); dots += `<span class="dot" style="background:${{REL_DOT[r]||'#ccc'}}"></span>`; }}
        }}

        html += `<td class="cal-cell ${{isToday}} ${{hasItems}}" ${{click}} title="${{items.length}} ärenden">
          <span class="day-num">${{day}}</span><div class="dots">${{dots}}</div></td>`;

        col++;
        if (col % 7 === 0 && day < daysInMonth) html += "</tr><tr>";
      }}
      // Fyll ut sista raden
      const remaining = (7 - (col % 7)) % 7;
      for (let i = 0; i < remaining; i++) html += '<td class="cal-empty-cell"></td>';
      html += "</tr>";

      document.getElementById("cal-body").innerHTML = html;
    }}

    function prevMonth() {{
      if (calMonth === 1) {{ calMonth = 12; calYear--; }} else calMonth--;
      renderCalendar(calYear, calMonth);
    }}

    function nextMonth() {{
      if (calMonth === 12) {{ calMonth = 1; calYear++; }} else calMonth++;
      renderCalendar(calYear, calMonth);
    }}

    renderCalendar(calYear, calMonth);
    </script>"""

    return f"""
    <section class="calendar-section">
      <h2 class="section-title">📅 Kalender</h2>
      <div class="cal-grid">
        <div class="cal-block">
          <h3 class="cal-block-title">Idag — {_swedish_date(today)}</h3>
          {today_html}
        </div>
        <div class="cal-block">
          <h3 class="cal-block-title">Kommande veckan</h3>
          {week_days_html}
        </div>
        <div class="cal-block">
          {month_cal_html}
        </div>
      </div>
    </section>
    {js_and_panel}"""


def _clean_title(item: dict) -> str:
    """Tvättar intetsägande RSS/byrå-rubriker. Använder första meningen av
    sammanfattningen som ersättningsrubrik när originaltiteln är opaque."""
    title = (item.get("title") or "Utan titel").strip()
    samm = (item.get("analysis", {}).get("sammanfattning") or "").strip()

    # Opaque-mönster där originaltiteln säger lite eller inget
    opaque_prefixes = (
        "Latest news -", "Highlights -", "Newsletters",
    )
    opaque_patterns = (
        "e-mail alert", "press release",
        "opinion ", "annual report ",
    )

    lt = title.lower()
    is_opaque = (
        any(title.startswith(p) for p in opaque_prefixes)
        or any(p in lt for p in opaque_patterns)
        or len(title) < 15
    )

    if is_opaque and samm:
        # Första meningen av sammanfattning, men inte längre än 100 tecken
        import re as _re
        first = _re.split(r"(?<=[.!?])\s+", samm, maxsplit=1)[0]
        first = first.strip().rstrip(".")
        if len(first) > 100:
            first = first[:100].rsplit(" ", 1)[0] + "…"
        if len(first) > 15:  # bara om vi har en vettig mening
            return first

    return title


def _date_mini_card(entry: dict) -> str:
    """Mini-card för ett viktigt datum (AI-extraherat från viktiga_datum-fältet).
    Visar beskrivning + länk till källdokumentet."""
    beskrivning = entry.get("beskrivning", "")
    title = entry.get("title", "")
    url = entry.get("url", "")
    arende = entry.get("arende", "")
    from urllib.parse import urlparse as _urlparse
    _url_specific = bool(url and _urlparse(url).path.strip("/"))
    link_attr = f'href="{url}" target="_blank"' if _url_specific else ""
    source_label = arende or title[:60] or "Viktigt datum"
    return f"""
    <div class="mini-card" style="border-left:3px solid #d68910">
      <a {link_attr} class="mini-title">📅 {beskrivning}</a>
      <p class="mini-meta">Viktigt datum · {source_label}</p>
    </div>"""


def _mini_card(item: dict) -> str:
    analysis = item.get("analysis", {})
    relevans = analysis.get("relevans", "okänd")
    color = RELEVANCE_COLOR.get(relevans, "#888")
    emoji = RELEVANCE_EMOJI.get(relevans, "⚪")
    title = _clean_title(item)
    url = item.get("url", "")
    from urllib.parse import urlparse as _urlparse
    _url_specific = bool(url and _urlparse(url).path.strip("/"))
    sammanfattning = analysis.get("sammanfattning", "")
    vinkel = analysis.get("tech_vinkel", "")
    source = item.get("source", "")
    item_type = item.get("type", "")
    meta_bits = [b for b in (source, item_type) if b]
    meta = " · ".join(meta_bits)
    link = f'href="{url}"' if _url_specific else ""

    # Mini-vy: bara första meningen av sammanfattning + tech-vinkel.
    # Full text finns i full-card och sido-panel.
    def _first_sentence(text: str, max_chars: int = 140) -> str:
        if not text:
            return ""
        # Leta efter första punkt/utropstecken/frågetecken inom rimligt avstånd
        for i, ch in enumerate(text[:max_chars + 40]):
            if ch in ".!?" and i > 30:
                return text[:i + 1]
        # Annars hård truncering på ordgräns
        if len(text) <= max_chars:
            return text
        return text[:max_chars].rsplit(" ", 1)[0] + "…"

    samm_short = _first_sentence(sammanfattning)
    vinkel_short = _first_sentence(vinkel)

    samm_html = f"<p class='mini-samm'>{samm_short}</p>" if samm_short else ""
    vinkel_html = f"<p class='mini-vinkel'><strong>Tech-vinkel:</strong> {vinkel_short}</p>" if vinkel_short else ""
    meta_html = f"<p class='mini-meta'>{meta}</p>" if meta else ""

    # Bädda in full text som HTML-attribut — låt klick expandera kortet
    import json as _json
    import html as _htmllib
    full_data = _htmllib.escape(_json.dumps({
        "title": title, "url": url, "source": source, "meta": meta,
        "sammanfattning": sammanfattning, "tech_vinkel": vinkel,
        "varfor": analysis.get("varfor_viktigt", ""),
        "eu_koppling": analysis.get("eu_koppling") or "",
    }, ensure_ascii=False), quote=True)

    return f"""
    <div class="mini-card mini-card-expandable" style="border-left:3px solid {color}" onclick="expandMini(this)" data-full="{full_data}">
      <div class="mini-card-head">
        <a {link} target="_blank" class="mini-title" onclick="event.stopPropagation()">{emoji} {title}</a>
        <span class="mini-expand-hint">▾</span>
      </div>
      {meta_html}
      {samm_html}
      {vinkel_html}
    </div>"""


def _full_card(item: dict) -> str:
    analysis = item.get("analysis", {})
    relevans = analysis.get("relevans", "okänd")
    emoji = RELEVANCE_EMOJI.get(relevans, "⚪")
    color = RELEVANCE_COLOR.get(relevans, "#888")
    label = RELEVANCE_LABEL.get(relevans, relevans)
    title = _clean_title(item)
    date_str = (item.get("date") or "")[:10]
    committee = item.get("committee", "")
    url = item.get("url", "")
    sammanfattning = analysis.get("sammanfattning", "")
    tech_vinkel = analysis.get("tech_vinkel", "")
    varfor = analysis.get("varfor_viktigt", "")
    eu_koppling = analysis.get("eu_koppling") or ""
    keywords = analysis.get("keywords", [])

    keyword_tags = " ".join(
        f'<span class="tag">{kw}</span>' for kw in keywords[:4]
    )
    eu_row = f'<p class="eu-link">🇪🇺 <strong>EU-koppling:</strong> {eu_koppling}</p>' if eu_koppling and eu_koppling != "null" else ""
    from urllib.parse import urlparse as _urlparse
    _url_specific = bool(url and _urlparse(url).path.strip("/"))
    url_row = f'<a class="read-more" href="{url}" target="_blank">Läs originaldokumentet →</a>' if _url_specific else ""
    meta = " · ".join(filter(None, [date_str, committee]))

    sammanfattning_html = (
        f'<p class="sammanfattning"><strong>Vad handlar det om?</strong> {sammanfattning}</p>'
        if sammanfattning else ""
    )
    vinkel_html = (
        f'<p class="vinkel"><strong>Tech-vinkel:</strong> {tech_vinkel}</p>'
        if tech_vinkel else ""
    )
    varfor_html = (
        f'<p class="varfor"><strong>Varför viktigt just nu:</strong> {varfor}</p>'
        if varfor else ""
    )

    return f"""
    <div class="card">
      <div class="card-header" style="border-left:4px solid {color}">
        <span class="relevance-badge" style="background:{color}">{emoji} {label}</span>
        <h3>{title}</h3>
        <p class="meta">{meta}</p>
      </div>
      <div class="card-body">
        {sammanfattning_html}
        {vinkel_html}
        {varfor_html}
        {eu_row}
        <div class="card-footer">
          <div>{keyword_tags}</div>
          {url_row}
        </div>
      </div>
    </div>"""


def _build_lookback_section(yesterday: list[dict], last_week: list[dict]) -> str:
    """Bygger 'Vad hände?' — gårdagen och veckan som gått."""
    if not yesterday and not last_week:
        return ""

    # Använd _mini_card — ger sammanfattning + tech-vinkel + klickbar expand
    yesterday_html = ""
    if yesterday:
        hog = [i for i in yesterday if i.get("analysis", {}).get("relevans") == "hög"]
        rest = [i for i in yesterday if i.get("analysis", {}).get("relevans") != "hög"]
        show = (hog + rest)[:6]
        yesterday_html = "".join(_mini_card(i) for i in show)
    else:
        yesterday_html = '<p class="cal-empty">Inga sparade ärenden från igår.</p>'

    week_html = ""
    if last_week:
        # Deduplisera och ta de mest relevanta
        seen = set()
        unique = []
        for item in last_week:
            key = item.get("title", "")
            if key not in seen:
                seen.add(key)
                unique.append(item)
        relevance_order = {"hög": 0, "medel": 1, "låg": 2, "okänd": 3}
        unique.sort(key=lambda x: relevance_order.get(x.get("analysis", {}).get("relevans", "okänd"), 3))
        show = unique[:8]
        week_html = "".join(_mini_card(i) for i in show)
    else:
        week_html = '<p class="cal-empty">Inga sparade ärenden från förra veckan.</p>'

    return f"""
    <section class="calendar-section">
      <h2 class="section-title">⏪ Vad hände?</h2>
      <div class="cal-grid">
        <div class="cal-block">
          <h3 class="cal-block-title">Igår</h3>
          {yesterday_html}
        </div>
        <div class="cal-block" style="grid-column: span 2">
          <h3 class="cal-block-title">Veckan som gick — toppärenden</h3>
          <div style="display:grid; grid-template-columns: 1fr 1fr; gap: 0.6rem">{week_html}</div>
        </div>
      </div>
    </section>"""


TEMA_EMOJI = {
    "AI": "🤖",
    "Cybersäkerhet": "🛡️",
    "Plattformsreglering": "📱",
    "Halvledare": "🔬",
    "Dataskydd och integritet": "🔒",
    "Digital infrastruktur": "🌐",
    "Uppkopplade fordon": "🚗",
    "Sociala medier": "💬",
    "Övrigt tech": "⚙️",
}

TEMA_ORDER = [
    "AI",
    "Cybersäkerhet",
    "Plattformsreglering",
    "Halvledare",
    "Dataskydd och integritet",
    "Digital infrastruktur",
    "Uppkopplade fordon",
    "Sociala medier",
    "Övrigt tech",
]

# Kort källetikett i dashboard-raden
SOURCE_SHORT = {
    "Riksdagen": "Riksdag",
    "Regeringen": "Reg",
    "EU-parlamentet": "EP",
    "EU-kommissionen": "EU-kom",
}


def _item_anchor(item: dict) -> str:
    """Stabil HTML-id för ett item (md5 av URL eller titel)."""
    import hashlib
    key = item.get("url") or item.get("title") or ""
    return "item-" + hashlib.md5(key.encode("utf-8")).hexdigest()[:10]


def _build_new_today_section(items: list[dict], today_date: date) -> str:
    """Bygger en 'Senaste 24h'-ruta: items vars cached_at är idag eller igår
    (rolling window) — så att items inte 'försvinner' bara för att man kör
    flera gånger samma dag eller passerar midnatt."""
    try:
        import memory as _mem
        cache = _mem.load_analysis_cache()
    except Exception:
        cache = {}

    today_iso = today_date.isoformat()
    yesterday_iso = (today_date - timedelta(days=1)).isoformat()
    # Filtrera bort uppenbart icke-tech: bara hög/medel släpps in
    ok_relevans = {"hög", "medel"}
    new_items: list[dict] = []
    items_by_url = {i.get("url"): i for i in items if i.get("url")}
    for url, entry in cache.items():
        cached_at = entry.get("cached_at", "")
        # Rolling 24h: ta med items från idag och igår
        if cached_at != today_iso and cached_at != yesterday_iso:
            continue
        analysis = entry.get("analysis") or {}
        if analysis.get("relevans") not in ok_relevans:
            continue
        # Föredra den item-instans som finns i items-listan (har all metadata);
        # fall tillbaka till cache-entryn om itemet klustrats bort
        item = items_by_url.get(url) or {
            "title": entry.get("title", ""),
            "url": url,
            "source": "",
            "analysis": analysis,
        }
        new_items.append(item)

    if not new_items:
        return f"""
        <section class="new-today-section new-today-empty">
          <h2 class="section-title">🆕 Nytt idag</h2>
          <p class="nt-empty">Inga nya tech-ärenden har dykt upp idag.</p>
        </section>"""

    # Sortera: hög relevans först
    relevance_order = {"hög": 0, "medel": 1, "låg": 2, "okänd": 3}
    new_items.sort(key=lambda x: relevance_order.get(x.get("analysis", {}).get("relevans", "okänd"), 3))

    import json as _json
    import html as _htmllib
    rows = ""
    for item in new_items:
        title = _clean_title(item)
        source = SOURCE_SHORT.get(item.get("source", ""), item.get("source", ""))
        analysis = item.get("analysis", {})
        relevans = analysis.get("relevans", "okänd")
        dot_color = RELEVANCE_DOT.get(relevans, "#ccc")
        # Bädda in full info — expandMini öppnar sidopanelen
        full_data = _htmllib.escape(_json.dumps({
            "title": title,
            "url": item.get("url", ""),
            "source": item.get("source", ""),
            "meta": source,
            "sammanfattning": analysis.get("sammanfattning", ""),
            "tech_vinkel": analysis.get("tech_vinkel", ""),
            "varfor": analysis.get("varfor_viktigt", ""),
            "eu_koppling": analysis.get("eu_koppling") or "",
        }, ensure_ascii=False), quote=True)
        rows += f"""
        <li onclick="expandMini(this)" data-full="{full_data}" class="nt-li-click">
          <span class="nt-dot" style="background:{dot_color}"></span>
          <span class="nt-link">{title}</span>
          <span class="nt-src">{source}</span>
        </li>"""

    return f"""
    <section class="new-today-section">
      <h2 class="section-title">🆕 Nytt idag <span class="nt-count">{len(new_items)}</span></h2>
      <ul class="nt-list">{rows}</ul>
    </section>"""


def _build_dashboard_section(items: list[dict], today_date: date) -> str:
    """Kompakt översikt grupperad per tech-tema. Ett klick → hoppa ned till full info."""
    if not items:
        return ""

    # Gruppera efter tema
    by_tema: dict[str, list[dict]] = {}
    for item in items:
        tema = item.get("analysis", {}).get("tema") or "Övrigt tech"
        if tema not in TEMA_ORDER:
            tema = "Övrigt tech"
        by_tema.setdefault(tema, []).append(item)

    # Sortera inom tema: nyast datum först (fallande)
    for tema in by_tema:
        by_tema[tema].sort(
            key=lambda x: _date_sort_key(x.get("date") or ""),
            reverse=True,
        )

    # Sortera teman efter senaste uppdatering (nyaste först)
    def _latest_date(items_list: list[dict]) -> int:
        return max((_date_sort_key(i.get("date") or "") for i in items_list), default=0)

    sorted_temas = sorted(
        [t for t in TEMA_ORDER if by_tema.get(t)],
        key=lambda t: -_latest_date(by_tema[t]),
    )

    blocks = ""
    for tema in sorted_temas:
        tema_items = by_tema[tema]
        if not tema_items:
            continue

        emoji = TEMA_EMOJI.get(tema, "•")
        rows = ""
        for item in tema_items:
            analysis = item.get("analysis", {})
            title = _clean_title(item)
            relevans = analysis.get("relevans", "okänd")
            dot_color = RELEVANCE_DOT.get(relevans, "#ccc")
            source = item.get("source", "")
            source_short = SOURCE_SHORT.get(source, source)

            # Badge för status + formaterat datum (alltid synligt)
            date_str = (item.get("date") or "")[:10]
            status_badge = ""
            date_label = ""
            if date_str:
                try:
                    d = date.fromisoformat(date_str)
                    date_label = _swedish_date(d)
                    if d == today_date:
                        status_badge = '<span class="dash-badge dash-badge-today">IDAG</span>'
                    elif d == today_date - timedelta(days=1):
                        status_badge = '<span class="dash-badge dash-badge-new">IGÅR</span>'
                except ValueError:
                    date_label = date_str
            date_html = f'<span class="dash-date">{date_label}</span>' if date_label else ""

            # Både kort sammanfattning (vad det är) och tech-vinkel (varför tech).
            # Full text finns i detaljvyn.
            sammanfattning = analysis.get("sammanfattning", "")
            samm_short = ""
            if sammanfattning:
                if len(sammanfattning) > 260:
                    samm_short = sammanfattning[:260].rsplit(" ", 1)[0] + "…"
                else:
                    samm_short = sammanfattning

            vinkel = analysis.get("tech_vinkel", "")
            if vinkel and len(vinkel) > 260:
                vinkel_short = vinkel[:260].rsplit(" ", 1)[0] + "…"
            else:
                vinkel_short = vinkel

            url = item.get("url", "")
            from urllib.parse import urlparse as _urlparse
            url_specific = bool(url and _urlparse(url).path.strip("/"))
            link_attr = f'href="{url}" target="_blank"' if url_specific else ""
            title_el = f'<a {link_attr} class="dash-title">{title}</a>' if link_attr else f'<span class="dash-title">{title}</span>'

            samm_html = (
                f"<p class='dash-samm'>{samm_short}</p>"
                if samm_short else ""
            )
            vinkel_html = (
                f"<p class='dash-vinkel'><span class='dash-vinkel-label'>Tech-vinkel:</span> {vinkel_short}</p>"
                if vinkel_short else ""
            )
            anchor = _item_anchor(item)
            rows += f"""
            <div class="dash-row" id="{anchor}">
              <span class="dash-dot" style="background:{dot_color}"></span>
              <div class="dash-main">
                {title_el}
                {samm_html}
                {vinkel_html}
              </div>
              <div class="dash-meta">
                {date_html}
                <span class="dash-source">{source_short}</span>
                {status_badge}
              </div>
            </div>"""

        blocks += f"""
        <div class="dash-tema">
          <h3 class="dash-tema-title">{emoji} {tema} <span class="dash-count">{len(tema_items)}</span></h3>
          <div class="dash-rows">{rows}</div>
        </div>"""

    return f"""
    <section class="dashboard-section">
      <h2 class="section-title">📊 Översikt per tema</h2>
      <div class="dash-grid">{blocks}</div>
    </section>"""


def _build_arenden_section(arenden: dict) -> str:
    """Bygger 'Pågående ärenden'-sektionen med tidslinje per ärende."""
    if not arenden:
        return ""

    # Sortera ärenden så det mest nyligen uppdaterade visas först
    sorted_arenden = sorted(
        arenden.items(),
        key=lambda kv: kv[1].get("last_updated", "") or "",
        reverse=True,
    )

    cards = ""
    for name, data in sorted_arenden:
        docs = data.get("documents", [])
        next_step = data.get("next_step", "")
        last_updated = data.get("last_updated", "")
        is_new = data.get("is_new_today", False)
        is_updated = data.get("is_updated_today", False)

        if is_new:
            badge = '<span class="arende-badge arende-badge-new">Nytt idag</span>'
        elif is_updated:
            badge = '<span class="arende-badge arende-badge-updated">Uppdaterat</span>'
        else:
            badge = ""

        # Tidslinje — sortera så NYASTE dokumentet visas överst inom varje ärende
        sorted_docs = sorted(
            docs,
            key=lambda d: _date_sort_key(d.get("date", "")),
            reverse=True,
        )
        timeline_rows = ""
        for doc in sorted_docs[:5]:
            doc_date = doc.get("date", "")[:10]
            doc_title = doc.get("title", "")
            doc_url = doc.get("url", "")
            doc_source = doc.get("source", "")
            doc_vinkel = doc.get("tech_vinkel", "")
            link_open = f'<a href="{doc_url}" target="_blank" style="color:#4a6cf7;text-decoration:none">' if doc_url else "<span>"
            link_close = "</a>" if doc_url else "</span>"
            timeline_rows += f"""
            <div class="tl-row">
              <div class="tl-date">{doc_date}</div>
              <div class="tl-dot"></div>
              <div class="tl-content">
                {link_open}<strong>{doc_title}</strong>{link_close}
                <span class="tl-source">{doc_source}</span>
                {"<p class='tl-vinkel'>" + doc_vinkel + "</p>" if doc_vinkel else ""}
              </div>
            </div>"""

        next_html = f'<div class="arende-next">⏭ <strong>Näst upp:</strong> {next_step}</div>' if next_step and next_step.lower() != "null" else ""

        # "Näst upp" flyttat till ovanför tidslinjen
        cards += f"""
        <div class="arende-card">
          <div class="arende-header">
            <span class="arende-name">{name}</span>
            <div style="display:flex;gap:0.4rem;align-items:center">
              {badge}
              <span class="arende-updated">{last_updated}</span>
            </div>
          </div>
          {next_html}
          <div class="tl-container">{timeline_rows}</div>
        </div>"""

    return f"""
    <section class="calendar-section">
      <h2 class="section-title">📂 Pågående ärenden</h2>
      <div class="arenden-grid">{cards}</div>
    </section>"""


def generate(items: list[dict], output_path: str = "digest.html",
             yesterday: Optional[list] = None,
             last_week: Optional[list] = None,
             arenden: Optional[dict] = None,
             important_dates: Optional[dict] = None) -> str:
    now = datetime.now()
    today = now.date()
    date_str = f"{today.day} {SWEDISH_MONTHS[today.month]} {today.year}"

    # Detaljerad vy sorteras på datum (nyast först). Inom källgruppering
    # hamnar nyaste items överst.
    items_sorted = sorted(
        items,
        key=lambda x: _date_sort_key(x.get("date", "")),
        reverse=True,
    )

    # Detaljerad vy: platt lista sorterad på datum, nyast först
    source_sections = "".join(_full_card(i) for i in items_sorted)

    dashboard_section = _build_dashboard_section(items, today)
    calendar_section = _build_calendar_section(items, important_dates or {})
    lookback_section = _build_lookback_section(yesterday or [], last_week or [])
    arenden_section = _build_arenden_section(arenden or {})
    new_today_section = _build_new_today_section(items, today)

    n_hog  = sum(1 for i in items if i.get("analysis", {}).get("relevans") == "hög")
    n_med  = sum(1 for i in items if i.get("analysis", {}).get("relevans") == "medel")

    html = f"""<!DOCTYPE html>
<html lang="sv">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Tech Vinklar — {date_str}</title>
<style>
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
    background: #f4f5f7;
    color: #1a1a2e;
    line-height: 1.6;
  }}
  header {{
    background: #1a1a2e;
    color: white;
    padding: 2rem 2.5rem;
  }}
  header h1 {{ font-size: 1.8rem; font-weight: 700; margin-bottom: 0.25rem; }}
  header p {{ color: #aab4c8; font-size: 0.95rem; }}
  .stats {{ display: flex; gap: 1rem; margin-top: 1rem; flex-wrap: wrap; }}
  .stat {{
    background: rgba(255,255,255,0.1);
    border-radius: 8px;
    padding: 0.35rem 0.85rem;
    font-size: 0.85rem;
  }}
  main {{ max-width: 960px; margin: 2rem auto; padding: 0 1.5rem 4rem; }}

  /* ── Nytt idag-ruta ── */
  .new-today-section {{
    margin-bottom: 2.5rem;
    background: linear-gradient(135deg, #fff8e1 0%, #fff3cd 100%);
    border: 1px solid #f0d97e;
    border-radius: 10px;
    padding: 1.1rem 1.4rem;
  }}
  .new-today-section .section-title {{ margin-bottom: 0.8rem; }}
  .nt-count {{
    display: inline-block;
    background: #c39000;
    color: white;
    font-size: 0.75rem;
    font-weight: 700;
    padding: 0.1rem 0.55rem;
    border-radius: 20px;
    margin-left: 0.3rem;
    vertical-align: middle;
  }}
  .nt-list {{ list-style: none; padding: 0; margin: 0; }}
  .nt-list li {{
    display: flex;
    align-items: center;
    gap: 0.5rem;
    padding: 0.35rem 0;
    border-top: 1px solid rgba(195, 144, 0, 0.15);
  }}
  .nt-list li:first-child {{ border-top: none; }}
  .nt-dot {{
    width: 8px;
    height: 8px;
    border-radius: 50%;
    flex-shrink: 0;
  }}
  .nt-link {{
    flex: 1;
    color: #1a1a2e;
    text-decoration: none;
    font-size: 0.9rem;
    font-weight: 500;
  }}
  .nt-li-click {{ cursor: pointer; transition: background 0.1s; }}
  .nt-li-click:hover {{ background: rgba(255,255,255,0.4); }}
  .nt-li-click:hover .nt-link {{ color: #4a6cf7; }}
  .nt-src {{
    font-size: 0.7rem;
    color: #8a7a3a;
    font-weight: 700;
    text-transform: uppercase;
    letter-spacing: 0.04em;
  }}
  .nt-empty {{
    font-size: 0.9rem;
    color: #8a7a3a;
    font-style: italic;
    margin-top: 0.3rem;
  }}

  /* ── Dashboard (översikt per tema) ── */
  .dashboard-section {{ margin-bottom: 2.5rem; }}
  .dash-grid {{
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(340px, 1fr));
    gap: 1.2rem;
  }}
  .dash-tema {{
    background: white;
    border-radius: 10px;
    padding: 1rem 1.1rem;
    box-shadow: 0 1px 3px rgba(0,0,0,0.05);
    border-top: 3px solid #4a6cf7;
  }}
  .dash-tema-title {{
    font-size: 0.95rem;
    font-weight: 700;
    color: #1a1a2e;
    margin-bottom: 0.7rem;
    display: flex;
    align-items: center;
    gap: 0.35rem;
  }}
  .dash-count {{
    margin-left: auto;
    font-size: 0.7rem;
    font-weight: 600;
    background: #f0f2f8;
    color: #4a4a5a;
    padding: 0.1rem 0.55rem;
    border-radius: 20px;
  }}
  .dash-rows {{ display: flex; flex-direction: column; gap: 0.5rem; }}
  .dash-row {{
    display: grid;
    grid-template-columns: 10px 1fr auto;
    gap: 0.5rem;
    align-items: start;
    padding: 0.45rem 0;
    border-top: 1px solid #f0f2f8;
  }}
  .dash-row:first-child {{ border-top: none; padding-top: 0.1rem; }}
  .dash-dot {{
    width: 8px;
    height: 8px;
    border-radius: 50%;
    margin-top: 0.45rem;
  }}
  .dash-main {{ min-width: 0; }}
  .dash-title {{
    font-size: 0.83rem;
    font-weight: 600;
    color: #1a1a2e;
    text-decoration: none;
    line-height: 1.35;
    display: block;
  }}
  .dash-title:hover {{ color: #4a6cf7; text-decoration: underline; }}
  .dash-samm {{
    font-size: 0.8rem;
    color: #2a2a3a;
    margin-top: 0.3rem;
    line-height: 1.5;
  }}
  .dash-vinkel {{
    font-size: 0.8rem;
    color: #3a3a4a;
    margin-top: 0.35rem;
    line-height: 1.45;
    padding: 0.4rem 0.55rem;
    background: #f7f9fd;
    border-left: 2px solid #c5d1ea;
    border-radius: 3px;
  }}
  .dash-vinkel-label {{
    font-weight: 700;
    color: #4a6cf7;
    margin-right: 0.2rem;
    font-size: 0.72rem;
    text-transform: uppercase;
    letter-spacing: 0.04em;
  }}
  .dash-meta {{
    display: flex;
    flex-direction: column;
    align-items: flex-end;
    gap: 0.2rem;
    font-size: 0.68rem;
  }}
  .dash-date {{
    color: #5a5a6a;
    font-weight: 600;
    font-size: 0.7rem;
  }}
  .dash-source {{
    color: #8a8a9a;
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: 0.04em;
  }}
  .dash-badge {{
    font-size: 0.6rem;
    font-weight: 700;
    padding: 0.08rem 0.4rem;
    border-radius: 20px;
    text-transform: uppercase;
    letter-spacing: 0.04em;
    white-space: nowrap;
  }}
  .dash-badge-today {{ background: #ffe4e0; color: #c33b24; }}
  .dash-badge-new {{ background: #fff3cd; color: #856404; }}

  /* ── Kalender ── */
  .calendar-section {{ margin-bottom: 2.5rem; }}
  .section-title {{
    font-size: 1.2rem; font-weight: 700;
    margin-bottom: 1.2rem; color: #1a1a2e;
  }}
  .cal-grid {{
    display: grid;
    grid-template-columns: 1fr 1fr 1fr;
    gap: 1.2rem;
  }}
  @media (max-width: 720px) {{ .cal-grid {{ grid-template-columns: 1fr; }} }}
  .cal-block {{
    background: white;
    border-radius: 10px;
    padding: 1.1rem 1.2rem;
    box-shadow: 0 1px 4px rgba(0,0,0,0.08);
  }}
  .cal-block-title {{
    font-size: 0.85rem;
    font-weight: 700;
    text-transform: uppercase;
    letter-spacing: 0.05em;
    color: #666;
    margin-bottom: 0.9rem;
  }}
  .cal-empty {{ color: #aaa; font-size: 0.88rem; font-style: italic; }}
  .mini-card {{
    padding: 0.5rem 0.7rem;
    margin-bottom: 0.6rem;
    border-radius: 6px;
    background: #f8f9fc;
  }}
  .mini-title {{
    font-size: 0.85rem;
    font-weight: 600;
    color: #1a1a2e;
    text-decoration: none;
    display: block;
    line-height: 1.35;
  }}
  .mini-title:hover {{ color: #4a6cf7; }}
  .mini-card-expandable {{
    cursor: pointer;
    transition: background 0.1s;
  }}
  .mini-card-expandable:hover {{ background: #eef1f7; }}
  .mini-card-head {{
    display: flex;
    justify-content: space-between;
    align-items: flex-start;
    gap: 0.4rem;
  }}
  .mini-expand-hint {{
    color: #aaa;
    font-size: 0.8rem;
    flex-shrink: 0;
  }}
  .mini-meta {{
    font-size: 0.7rem;
    color: #8a8a9a;
    margin-top: 0.2rem;
    text-transform: uppercase;
    letter-spacing: 0.03em;
  }}
  .mini-samm {{
    font-size: 0.82rem;
    color: #3a3a4a;
    margin-top: 0.4rem;
    line-height: 1.5;
  }}
  .mini-vinkel {{
    font-size: 0.78rem;
    color: #4a4a5a;
    margin-top: 0.35rem;
    line-height: 1.4;
    padding-top: 0.35rem;
    border-top: 1px dashed #dde2ee;
  }}
  .week-day {{ margin-bottom: 1rem; }}
  .week-day-label {{
    font-size: 0.8rem;
    font-weight: 700;
    color: #4a6cf7;
    margin-bottom: 0.4rem;
    text-transform: uppercase;
    letter-spacing: 0.04em;
  }}
  /* Månadskalender */
  .cal-nav {{
    display: flex;
    align-items: center;
    justify-content: space-between;
    margin-bottom: 0.5rem;
  }}
  .cal-nav-btn {{
    background: none;
    border: 1px solid #dde2ee;
    border-radius: 6px;
    padding: 0.2rem 0.6rem;
    cursor: pointer;
    color: #4a6cf7;
    font-size: 0.8rem;
  }}
  .cal-nav-btn:hover:not(:disabled) {{ background: #eef2ff; }}
  .month-cal {{ width: 100%; border-collapse: collapse; }}
  .month-cal th {{
    font-size: 0.7rem;
    color: #999;
    padding: 0.3rem 0;
    text-align: center;
    font-weight: 600;
  }}
  .cal-cell {{
    text-align: center;
    padding: 0.3rem 0.1rem;
    vertical-align: top;
    width: 14.28%;
  }}
  .cal-cell.has-items {{
    cursor: pointer;
  }}
  .cal-cell.has-items:hover {{
    background: #eef2ff;
    border-radius: 6px;
  }}
  .cal-cell.today .day-num {{
    background: #4a6cf7;
    color: white;
    border-radius: 50%;
    width: 22px;
    height: 22px;
    display: inline-flex;
    align-items: center;
    justify-content: center;
  }}
  .cal-empty-cell {{ background: none; }}
  .day-num {{ font-size: 0.82rem; color: #333; }}
  .dots {{ display: flex; justify-content: center; gap: 2px; margin-top: 2px; }}
  .dot {{
    width: 6px; height: 6px;
    border-radius: 50%;
    display: inline-block;
  }}
  .cal-legend {{
    display: flex;
    gap: 1rem;
    margin-top: 0.8rem;
    font-size: 0.75rem;
    color: #666;
    flex-wrap: wrap;
  }}
  .cal-legend span {{ display: flex; align-items: center; gap: 4px; }}

  /* ── Ärenden ── */
  h2.source-header {{
    font-size: 1rem;
    font-weight: 600;
    color: #555;
    text-transform: uppercase;
    letter-spacing: 0.05em;
    margin: 2.5rem 0 1rem;
    display: flex;
    align-items: center;
    gap: 0.5rem;
  }}
  .count {{
    background: #dde2ee;
    color: #444;
    border-radius: 20px;
    padding: 0.1rem 0.6rem;
    font-size: 0.8rem;
    font-weight: 500;
    text-transform: none;
    letter-spacing: 0;
  }}
  .card {{
    background: white;
    border-radius: 10px;
    margin-bottom: 1.1rem;
    box-shadow: 0 1px 4px rgba(0,0,0,0.08);
    overflow: hidden;
  }}
  .card-header {{ padding: 1rem 1.2rem 0.6rem; }}
  .card-header h3 {{
    font-size: 1rem;
    font-weight: 600;
    margin: 0.4rem 0 0.2rem;
    line-height: 1.4;
  }}
  .relevance-badge {{
    display: inline-block;
    color: white;
    font-size: 0.72rem;
    font-weight: 600;
    padding: 0.15rem 0.6rem;
    border-radius: 20px;
    text-transform: uppercase;
    letter-spacing: 0.04em;
  }}
  .meta {{ font-size: 0.8rem; color: #888; margin-top: 0.2rem; }}
  .card-body {{ padding: 0.8rem 1.2rem 1rem; border-top: 1px solid #f0f0f0; }}
  .sammanfattning {{
    background: #f6f8fc; border-left: 3px solid #cdd7ec;
    padding: 0.7rem 0.9rem; border-radius: 4px;
    font-size: 0.95rem; line-height: 1.55; color: #2a2a3a;
    margin-bottom: 0.7rem;
  }}
  .vinkel {{ font-weight: 500; margin-bottom: 0.5rem; color: #1a1a2e; line-height: 1.5; }}
  .varfor {{ font-size: 0.93rem; color: #3a3a4a; margin-bottom: 0.5rem; line-height: 1.55; }}
  .eu-link {{ font-size: 0.88rem; color: #2c5282; margin-top: 0.5rem; }}
  .card-footer {{
    display: flex;
    justify-content: space-between;
    align-items: center;
    margin-top: 0.8rem;
    flex-wrap: wrap;
    gap: 0.5rem;
  }}
  .tag {{
    display: inline-block;
    background: #eef2ff;
    color: #4a6cf7;
    font-size: 0.75rem;
    padding: 0.15rem 0.55rem;
    border-radius: 20px;
    margin-right: 0.3rem;
  }}
  .read-more {{
    font-size: 0.82rem;
    color: #4a6cf7;
    text-decoration: none;
    font-weight: 500;
  }}
  .read-more:hover {{ text-decoration: underline; }}
  /* ── Datum-panel ── */
  .day-panel {{
    display: none;
    position: fixed;
    inset: 0;
    background: rgba(0,0,0,0.35);
    z-index: 1000;
    align-items: flex-start;
    justify-content: flex-end;
  }}
  .day-panel.open {{ display: flex; }}
  .day-panel-inner {{
    background: white;
    width: min(480px, 95vw);
    height: 100vh;
    overflow-y: auto;
    box-shadow: -4px 0 24px rgba(0,0,0,0.15);
    animation: slideIn 0.22s ease;
  }}
  @keyframes slideIn {{
    from {{ transform: translateX(100%); }}
    to   {{ transform: translateX(0); }}
  }}
  .day-panel-header {{
    display: flex;
    justify-content: space-between;
    align-items: center;
    padding: 1.2rem 1.4rem;
    background: #1a1a2e;
    color: white;
    font-weight: 700;
    font-size: 1rem;
    position: sticky;
    top: 0;
  }}
  .close-btn {{
    background: none;
    border: none;
    color: white;
    font-size: 1.2rem;
    cursor: pointer;
    padding: 0.2rem 0.5rem;
    border-radius: 4px;
  }}
  .close-btn:hover {{ background: rgba(255,255,255,0.15); }}
  #day-panel-body {{ padding: 1rem 1.2rem; }}
  .panel-card {{
    background: #f8f9fc;
    border-radius: 8px;
    padding: 0.9rem 1rem;
    margin-bottom: 1rem;
  }}
  .panel-badge {{
    display: inline-block;
    color: white;
    font-size: 0.7rem;
    font-weight: 700;
    padding: 0.12rem 0.55rem;
    border-radius: 20px;
    text-transform: uppercase;
    letter-spacing: 0.04em;
    margin-bottom: 0.4rem;
  }}
  .panel-title {{ font-size: 0.95rem; font-weight: 600; line-height: 1.4; margin-bottom: 0.25rem; }}
  .panel-meta {{ font-size: 0.78rem; color: #888; margin-bottom: 0.5rem; }}
  .panel-samm {{
    background: #f6f8fc; border-left: 3px solid #cdd7ec;
    padding: 0.5rem 0.7rem; border-radius: 4px;
    font-size: 0.85rem; line-height: 1.5; color: #2a2a3a;
    margin-bottom: 0.5rem;
  }}
  .panel-vinkel {{ font-size: 0.88rem; font-weight: 500; margin-bottom: 0.4rem; line-height: 1.45; }}
  .panel-varfor {{ font-size: 0.85rem; color: #444; margin-bottom: 0.4rem; line-height: 1.5; }}
  .panel-eu {{ font-size: 0.82rem; color: #2c5282; margin-bottom: 0.5rem; }}
  .panel-read-more {{ font-size: 0.82rem; color: #4a6cf7; text-decoration: none; font-weight: 500; }}
  .panel-read-more:hover {{ text-decoration: underline; }}
  /* ── Pågående ärenden ── */
  .arenden-grid {{
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(300px, 1fr));
    gap: 1.2rem;
  }}
  .arende-card {{
    background: white;
    border-radius: 10px;
    padding: 1.1rem 1.2rem;
    box-shadow: 0 1px 4px rgba(0,0,0,0.08);
    border-top: 3px solid #4a6cf7;
  }}
  .arende-header {{
    display: flex;
    justify-content: space-between;
    align-items: baseline;
    margin-bottom: 0.9rem;
    flex-wrap: wrap;
    gap: 0.3rem;
  }}
  .arende-name {{
    font-size: 0.95rem;
    font-weight: 700;
    color: #1a1a2e;
  }}
  .arende-updated {{
    font-size: 0.72rem;
    color: #aaa;
  }}
  .arende-badge {{
    font-size: 0.68rem;
    font-weight: 700;
    padding: 0.1rem 0.5rem;
    border-radius: 20px;
    text-transform: uppercase;
    letter-spacing: 0.04em;
  }}
  .arende-badge-new {{ background: #d4edda; color: #155724; }}
  .arende-badge-updated {{ background: #fff3cd; color: #856404; }}
  .tl-container {{ display: flex; flex-direction: column; gap: 0; }}
  .tl-row {{
    display: grid;
    grid-template-columns: 62px 12px 1fr;
    gap: 0 0.5rem;
    align-items: start;
    padding: 0.35rem 0;
    border-left: none;
  }}
  .tl-date {{
    font-size: 0.72rem;
    color: #999;
    padding-top: 0.15rem;
    text-align: right;
  }}
  .tl-dot {{
    width: 8px;
    height: 8px;
    border-radius: 50%;
    background: #4a6cf7;
    margin-top: 0.3rem;
    flex-shrink: 0;
    position: relative;
  }}
  .tl-dot::after {{
    content: '';
    position: absolute;
    left: 3px;
    top: 8px;
    width: 2px;
    height: calc(100% + 0.7rem);
    background: #dde2ee;
  }}
  .tl-row:last-child .tl-dot::after {{ display: none; }}
  .tl-content {{ font-size: 0.82rem; line-height: 1.4; padding-bottom: 0.4rem; }}
  .tl-source {{
    display: inline-block;
    font-size: 0.7rem;
    color: #aaa;
    margin-left: 0.4rem;
  }}
  .tl-vinkel {{
    font-size: 0.78rem;
    color: #555;
    margin-top: 0.15rem;
    font-style: italic;
  }}
  .arende-next {{
    font-size: 0.8rem;
    color: #2c5282;
    background: #eef2ff;
    border-radius: 6px;
    padding: 0.4rem 0.7rem;
    margin-top: 0.7rem;
  }}
  footer {{
    text-align: center;
    color: #aaa;
    font-size: 0.8rem;
    padding: 2rem;
  }}
</style>
</head>
<body>
<header>
  <h1>🔍 Tech Vinklar</h1>
  <p>EU &amp; Riksdagen · {date_str}</p>
  <div class="stats">
    <div class="stat">📋 {len(items)} ärenden</div>
    <div class="stat">🔴 {n_hog} hög prioritet</div>
    <div class="stat">🟡 {n_med} medel</div>
  </div>
</header>
<main>
  {new_today_section}
  {calendar_section}
  {lookback_section}
  {dashboard_section}
  {arenden_section}
  <h2 class="section-title">📰 Alla ärenden — detaljerad vy</h2>
  {source_sections}
</main>
<footer>Genererad {now.strftime('%Y-%m-%d %H:%M')} · Tech Vinklar Agent</footer>
</body>
</html>"""

    with open(output_path, "w", encoding="utf-8") as f:
        f.write(html)

    return output_path
