"""
Genererar en snygg HTML-rapport med kalendervy av analyserade ärenden.
Öppnas i webbläsaren, kopieras enkelt in i Google Docs.
"""
import calendar
import html as html_lib
import json as _json_top
from collections import defaultdict
from datetime import datetime, date, timedelta
from typing import Optional
from urllib.parse import urlparse as _urlparse_top


def _esc(text) -> str:
    """HTML-säker text: skyddar mot XSS från RSS-titlar med <script>, &, " etc.
    Förvandlar None till tom sträng."""
    if text is None:
        return ""
    return html_lib.escape(str(text), quote=True)


def _safe_url(url) -> str:
    """Tillåt bara http(s)-URL:er som länkar. javascript:/data:/file:-URL:er
    från trasiga RSS-källor blockeras så att en URL inte kan köra kod."""
    if not url:
        return ""
    s = str(url).strip()
    try:
        scheme = _urlparse_top(s).scheme.lower()
    except ValueError:
        return ""
    if scheme not in ("http", "https"):
        return ""
    return _esc(s)


def _js_safe(text) -> str:
    """JSON-encodea sträng för inbäddning i JavaScript-mall — skyddar mot
    </script>-injection och kontrollerar tecken."""
    if text is None:
        return '""'
    return _json_top.dumps(str(text), ensure_ascii=False).replace("</", "<\\/")


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
    """Tolkar både ISO (2026-06-30) och RFC 822 (Mon, 01 Jan 2026 12:00:00 +0000).
    Sex RSS-källor sparar pubDate i RFC 822-format som .strptime inte klarade
    av — vilket fick ~70% av items att hamna utanför kalender och denna-vecka-vyn."""
    if not date_str:
        return None
    s = date_str.strip()
    # ISO först (vanligast)
    try:
        return datetime.fromisoformat(s[:19].replace("Z", "")).date()
    except ValueError:
        pass
    # RFC 822 (RSS pubDate) — hanterar alla varianter inkl tidszoner
    from email.utils import parsedate_to_datetime
    try:
        return parsedate_to_datetime(s).date()
    except (TypeError, ValueError):
        pass
    # Fallback: strptime med varianter — försök både med och utan trailing chars
    for fmt in ("%Y-%m-%d", "%Y-%m-%dT%H:%M:%S", "%a, %d %b %Y"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            pass
        # RSS-datum kan komma utan tid ("Tue, 02 Jun 2026") — trimma till exakt längd
        try:
            trimmed = s[:16]  # täcker "Tue, 02 Jun 2026" (16 tecken)
            return datetime.strptime(trimmed, fmt).date()
        except ValueError:
            continue
    return None


def _swedish_date(d: date) -> str:
    return f"{d.day} {SWEDISH_MONTHS[d.month]}"


def _format_item_date(raw: str) -> str:
    """Formaterar item-datum till 'DD MMM YYYY' på svenska.
    Hanterar både ISO ('2026-06-29') och RSS/RFC822 ('Fri, 29 May 2026...').
    Returnerar tom sträng om parsning misslyckas."""
    d = _parse_date(raw or "")
    if d is None:
        return ""
    return f"{d.day} {SWEDISH_MONTHS[d.month]} {d.year}"


def _date_sort_key(date_str: str) -> int:
    """Datum som heltal (YYYYMMDD) för sortering. Tolkar både ISO och RFC 822
    så att RSS-datum sorteras korrekt (förut blev 'Mon, 01 Jan' till 0)."""
    if not date_str:
        return 0
    d = _parse_date(date_str)
    if d:
        return int(d.strftime("%Y%m%d"))
    return 0


_GENERIC_DESC_STARTS = (
    "den ", "det ", "denna ", "detta ", "de ", "en ", "ett ",
    "rapport ", "rapporten ", "beslutet ", "mötet ", "utfallet ",
    "propositionen ", "utskottet ", "beskrivningen ", "förslaget ",
    "förhandlingen ", "överenskommelsen ", "resultatet ",
    "uppdraget ", "granskningen ", "utredningen ",
)


def _contextualize_date_description(
    beskrivning: str, src_title: str, arende: str
) -> str:
    """Prefixar en kalender-händelse med källdokumentets kontext om beskrivningen
    står ensam ('Den gemensamma mallen träder i kraft' → 'GDPR-anmälningsmall:
    Träder i kraft'). Kontext = ärendenamn (om finns) eller renskuren titel."""
    if not beskrivning:
        return beskrivning
    b = beskrivning.strip()
    b_low = b.lower()
    # Bestäm kontext-strängen — arende om finns, annars titel-derivat
    context = (arende or "").strip()
    if not context and src_title:
        clean_title = src_title.strip()
        # Skippa opake RSS-prefix
        for prefix in ("Latest news -", "Highlights -", "Next Committee",
                       "Committee on ", "Meeting of", "News from"):
            if clean_title.startswith(prefix):
                clean_title = ""
                break
        # Långa titlar → klipp vid naturlig brytpunkt så vi får ett kort ledord
        if clean_title and len(clean_title) > 60:
            for br in (": ", " — ", " – ", " - ", ", "):
                idx = clean_title.find(br, 8, 55)
                if idx > 0:
                    clean_title = clean_title[:idx]
                    break
            else:
                # Ingen brytpunkt: ta första 5 orden
                clean_title = " ".join(clean_title.split()[:5])
        if clean_title and len(clean_title) > 6:
            context = clean_title
    if not context:
        return b
    # Om kontexten redan finns i beskrivningen — lämna
    if len(context) > 6 and context.lower() in b_low:
        return b
    # Om beskrivningen börjar kontext-löst (pronomen/generisk term) → prefixa
    starts_generic = any(b_low.startswith(p) for p in _GENERIC_DESC_STARTS)
    if starts_generic:
        return f"{context}: {b}"
    return b


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
            raw_beskr = e.get("beskrivning", "")
            src_url = e.get("url", "")
            # Dedupera mot allt som redan finns på datumet
            if raw_beskr in existing_beskrivningar:
                continue
            if src_url and src_url in existing_urls:
                continue
            # Hämta sammanfattning + tech-vinkel från källdokumentet
            src_title = e.get("title", "")
            src_analysis = title_to_analysis.get(src_title, {})
            # Prefixa beskrivningen med ärende/titel om den saknar kontext
            beskrivning = _contextualize_date_description(
                raw_beskr, src_title, e.get("arende", "") or ""
            )
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

    # JSON-serialisera + skydda mot </script>-injection från RSS-titlar.
    # En RSS-titel som råkar innehålla "</script>" skulle annars kunna stänga
    # vår script-tagg och köra godtycklig kod i webbläsaren.
    js_data_str = _json.dumps(js_data, ensure_ascii=False).replace("</", "<\\/")

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
      const url = el.dataset.url || d.url || "";
      const rel = el.dataset.relevans || "okänd";
      const color = RELEVANCE_COLOR[rel] || "#888";
      const emoji = RELEVANCE_EMOJI[rel] || "⚪";
      const relLabel = ({{"hög":"Hög prioritet","medel":"Medel","låg":"Låg","okänd":"Okänd"}})[rel] || rel;
      document.getElementById("day-panel-title").textContent = d.title;
      const body = document.getElementById("day-panel-body");
      // Native <select> för prio — funkar oavsett iframe/panel-context
      const badge = `<div class="panel-card" data-url="${{url}}" data-relevans="${{rel}}">
        <select class="prio-select" style="background:${{color}}"
                onchange="setPrio(this.closest('.panel-card').dataset.url, this.value); this.blur();"
                title="Ändra prioritet">
          <option value="hög" ${{rel === "hög" ? "selected" : ""}}>🔴 Hög prioritet</option>
          <option value="medel" ${{rel === "medel" ? "selected" : ""}}>🟡 Medel</option>
          <option value="låg" ${{rel === "låg" ? "selected" : ""}}>🟢 Låg</option>
          <option value="utesluten" ${{rel === "utesluten" ? "selected" : ""}}>🚫 Uteslut från rapport</option>
        </select>`;
      const meta = d.meta ? `<p class="panel-meta">${{d.meta}}</p>` : "";
      const samm = d.sammanfattning ? `<p class="panel-samm"><strong>Vad handlar det om?</strong> ${{d.sammanfattning}}</p>` : "";
      const vinkel = d.tech_vinkel ? `<p class="panel-vinkel"><strong>Tech-vinkel:</strong> ${{d.tech_vinkel}}</p>` : "";
      const varfor = d.varfor ? `<p class="panel-varfor"><strong>Varför viktigt:</strong> ${{d.varfor}}</p>` : "";
      const eu = d.eu_koppling ? `<p class="panel-eu">🇪🇺 ${{d.eu_koppling}}</p>` : "";
      const link = d.url ? `<a href="${{d.url}}" target="_blank" class="panel-read-more">Läs originaldokumentet →</a>` : "";
      body.innerHTML = `${{badge}}${{meta}}${{samm}}${{vinkel}}${{varfor}}${{eu}}${{link}}</div>`;
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

    // ── Tryckbara filter på prioritet / ärende / keyword ──
    let activeFilter = null;  // {{type, value}}

    function filterCards(type, value) {{
      if (!value) return;
      // Klick på samma filter → toggla av
      if (activeFilter && activeFilter.type === type && activeFilter.value === value) {{
        resetFilter();
        return;
      }}
      activeFilter = {{type: type, value: value}};
      const cards = document.querySelectorAll('.card');
      let matchCount = 0;
      cards.forEach(c => {{
        const attr = c.dataset[type] || '';
        let match = false;
        if (type === 'keyword') {{
          // keywords är en kommaseparerad lista
          match = attr.split(',').includes(value);
        }} else {{
          match = attr === value;
        }}
        if (match) {{ c.classList.remove('filtered-out'); matchCount++; }}
        else {{ c.classList.add('filtered-out'); }}
      }});
      showFilterBar(type, value, matchCount);
      // Scrolla mjukt upp så hon ser filtret
      window.scrollTo({{top: 0, behavior: 'smooth'}});
    }}

    function resetFilter() {{
      activeFilter = null;
      document.querySelectorAll('.card').forEach(c => c.classList.remove('filtered-out'));
      const bar = document.getElementById('filter-bar');
      if (bar) bar.classList.remove('active');
    }}

    function showFilterBar(type, value, count) {{
      const bar = document.getElementById('filter-bar');
      if (!bar) return;
      const typeLabel = ({{relevans: 'prioritet', arende: 'ärende', keyword: 'tagg'}})[type] || type;
      bar.querySelector('.filter-bar-label').innerHTML =
        `🔍 Visar ${{count}} ärenden filtrerade på ${{typeLabel}}:`;
      bar.querySelector('.filter-bar-value').textContent = value;
      bar.classList.add('active');
    }}

    // ── Manuella prioritet-overrides ─────────────────────────
    const OVERRIDES_KEY = "tv_relevans_overrides_v1";
    // GitHub-konfiguration injiceras av Streamlit vid rendering.
    // OK att exponera i HTML eftersom sajten är privat (bara inloggade GitHub-konton ser den).
    const GITHUB_CONFIG = __GITHUB_CONFIG_PLACEHOLDER__;
    const REL_CYCLE = ["hög", "medel", "låg"];
    const REL_LABEL = {{"hög": "Hög prioritet", "medel": "Medel", "låg": "Låg", "utesluten": "Utesluten"}};
    const REL_MENU_ITEMS = [
      {{val: "hög",       label: "Hög prioritet",     color: "#c0392b", dot: "#e74c3c"}},
      {{val: "medel",     label: "Medel",             color: "#d68910", dot: "#f39c12"}},
      {{val: "låg",       label: "Låg",               color: "#27ae60", dot: "#2ecc71"}},
      {{val: "utesluten", label: "Uteslut från rapport", color: "#6b7280", dot: "#9ca3af", exclude: true}},
    ];

    function loadOverrides() {{
      try {{ return JSON.parse(localStorage.getItem(OVERRIDES_KEY) || "{{}}"); }}
      catch (e) {{ return {{}}; }}
    }}
    function saveOverrides(o) {{
      localStorage.setItem(OVERRIDES_KEY, JSON.stringify(o));
      updateSaveBar();
    }}

    function applyOverridesOnLoad() {{
      const o = loadOverrides();
      // Fullkort + mini-kort + dashboard-rader
      document.querySelectorAll('.card, .mini-card, .dash-row').forEach(card => {{
        const url = card.dataset.url;
        if (!url || !o[url]) return;
        const val = o[url];
        if (val === 'utesluten') {{
          card.classList.add('excluded');
          card.dataset.relevans = 'utesluten';
        }} else {{
          updateCardRelevans(card, val, true);
        }}
      }});
      // 'Nytt idag' visar bara hög — göm de items där override sagt lägre prio
      document.querySelectorAll('.nt-li-click').forEach(li => {{
        const url = li.dataset.url;
        if (url && o[url] && o[url] !== 'hög') {{
          li.classList.add('nt-hidden-by-prio');
        }}
      }});
      updateSaveBar();
      updateExcludedBanner();
    }}

    // Räkna uteslutna items och visa flärp — grupperar per URL så
    // varje ärende räknas EN gång även om det syns i flera sektioner
    function updateExcludedBanner() {{
      const excluded = new Set();
      document.querySelectorAll('.card.excluded, .mini-card.excluded, .dash-row.excluded, .panel-card.excluded').forEach(c => {{
        if (c.dataset.url) excluded.add(c.dataset.url);
      }});
      const banner = document.getElementById('excluded-banner');
      if (!banner) return;
      if (excluded.size > 0) {{
        banner.querySelector('.count').textContent = excluded.size;
        banner.classList.add('active');
      }} else {{
        banner.classList.remove('active');
      }}
    }}

    // Toggla mellan att visa/dölja uteslutna items
    let excludedVisible = false;
    function toggleExcludedVisible() {{
      excludedVisible = !excludedVisible;
      document.querySelectorAll('.card.excluded, .mini-card.excluded, .dash-row.excluded, .panel-card.excluded').forEach(c => {{
        c.classList.toggle('showing', excludedVisible);
      }});
      const btn = document.getElementById('excluded-toggle-btn');
      if (btn) btn.textContent = excludedVisible ? 'Göm igen' : 'Visa dem';
    }}

    // Öppna dropdown-menyn vid klick på prioritet-badge (fullkort eller mini)
    function openPrioMenu(trigger, ev) {{
      if (ev) ev.stopPropagation();
      closePrioMenu();  // stäng ev befintlig
      const card = trigger.closest('.card, .mini-card');
      if (!card) return;
      const url = card.dataset.url;
      if (!url) {{
        alert("Saknar URL — kan inte spara ändring för detta ärende.");
        return;
      }}
      const current = card.dataset.relevans;
      const menu = document.createElement('div');
      menu.className = 'prio-menu';
      menu.id = 'active-prio-menu';
      menu.innerHTML = REL_MENU_ITEMS.map(m => `
        <div class="prio-menu-item ${{m.exclude ? 'exclude' : ''}} ${{m.val === current ? 'current' : ''}}"
             data-val="${{m.val}}">
          <span class="dot" style="background:${{m.dot}}"></span>
          <span>${{m.label}}</span>
        </div>
      `).join('');
      document.body.appendChild(menu);
      // Positionera menyn under badgen (position: fixed = viewport-koordinater)
      const rect = trigger.getBoundingClientRect();
      menu.style.top = (rect.bottom + 6) + 'px';
      menu.style.left = rect.left + 'px';
      // Om menyn sticker ut åt höger, justera
      const menuRect = menu.getBoundingClientRect();
      if (menuRect.right > window.innerWidth - 10) {{
        menu.style.left = (window.innerWidth - menuRect.width - 10) + 'px';
      }}
      // Klick-handler för menyval
      menu.querySelectorAll('.prio-menu-item').forEach(el => {{
        el.addEventListener('click', (e) => {{
          e.stopPropagation();
          const val = el.dataset.val;
          setPrio(url, val);
          closePrioMenu();
        }});
      }});
      // Stäng vid klick utanför
      setTimeout(() => document.addEventListener('click', closePrioMenu, {{ once: true }}), 10);
    }}

    function closePrioMenu() {{
      const existing = document.getElementById('active-prio-menu');
      if (existing) existing.remove();
    }}

    // Sätt prioritet på alla element med samma URL — synkar över kort, mini-kort och dashboard
    function setPrio(url, newVal) {{
      // Query alla card/mini-card/dash-row samt panel-card i sidopanelen
      document.querySelectorAll(`.card[data-url="${{CSS.escape(url)}}"], .mini-card[data-url="${{CSS.escape(url)}}"], .dash-row[data-url="${{CSS.escape(url)}}"], .panel-card[data-url="${{CSS.escape(url)}}"]`).forEach(card => {{
        if (newVal === 'utesluten') {{
          card.classList.add('excluded');
          card.classList.remove('showing');  // start med gömd
          card.dataset.relevans = 'utesluten';
        }} else {{
          card.classList.remove('excluded', 'showing');
          updateCardRelevans(card, newVal, true);
        }}
      }});
      // 'Nytt idag' visar bara hög-prio — dölj items som blivit medel/låg/utesluten
      document.querySelectorAll(`.nt-li-click[data-url="${{CSS.escape(url)}}"]`).forEach(li => {{
        if (newVal === 'hög') {{
          li.classList.remove('nt-hidden-by-prio');
        }} else {{
          li.classList.add('nt-hidden-by-prio');
        }}
      }});
      const overrides = loadOverrides();
      overrides[url] = newVal;
      saveOverrides(overrides);
      updateExcludedBanner();
      // Auto-spara till GitHub efter kort debounce så vi inte spammar API:t
      // vid snabba ändringar. En knapp finns kvar som fallback.
      scheduleAutoSave();
    }}

    // Batch flera ändringar inom 1 sekund till EN sparning.
    let _autoSaveTimer = null;
    function scheduleAutoSave() {{
      if (!GITHUB_CONFIG.enabled) {{
        // Visa tydligt fel istället för tyst avbrott
        showSaveToast('⚠ PAT eller repo saknas — sparning gick inte igenom', true);
        return;
      }}
      if (_autoSaveTimer) clearTimeout(_autoSaveTimer);
      _autoSaveTimer = setTimeout(() => {{
        _autoSaveTimer = null;
        saveToRepo();
      }}, 1000);
    }}

    function updateCardRelevans(card, newRel, manual) {{
      card.dataset.relevans = newRel;
      card.classList.toggle('manually-set', !!manual);
      const color = RELEVANCE_COLOR[newRel] || "#888";
      const emoji = RELEVANCE_EMOJI[newRel] || "⚪";
      const label = REL_LABEL[newRel] || newRel;
      // Dashboard-rad: uppdatera dash-dot bakgrundsfärg
      if (card.classList.contains('dash-row')) {{
        const dot = card.querySelector('.dash-dot');
        if (dot) dot.style.background = color;
        return;  // dash-rader har ingen badge/select att uppdatera
      }}
      // Mini-prio-emoji-cirkel (utan dropdown)
      const miniEmoji = card.querySelector('.mini-prio-emoji');
      if (miniEmoji) {{
        miniEmoji.style.background = color;
        miniEmoji.textContent = emoji;
        miniEmoji.setAttribute('title', `Prioritet: ${{label}}`);
      }}
      // Fullkort: uppdatera <select> så vald option matchar samt bakgrundsfärg
      const sel = card.querySelector('.prio-select, .mini-prio-select');
      if (sel) {{
        sel.value = newRel;
        sel.style.background = color;
      }}
      // Bakåtkompatibilitet: en gammal <span class="relevance-badge"> kan finnas
      // i legacy-rapporter — uppdatera den också om den finns
      const badge = card.querySelector('.relevance-badge');
      if (badge) {{
        badge.style.background = color;
        badge.innerHTML = `${{emoji}} ${{label}}`;
      }}
      const header = card.querySelector('.card-header');
      if (header) header.style.borderLeft = `4px solid ${{color}}`;
      // Mini-kort: uppdatera vänsterkanten + prio-trigger-cirkel
      if (card.classList.contains('mini-card')) {{
        card.style.borderLeft = `3px solid ${{color}}`;
        const miniTrig = card.querySelector('.mini-prio-trigger');
        if (miniTrig) {{
          miniTrig.style.background = color;
          miniTrig.textContent = emoji;
        }}
      }}
    }}

    function updateSaveBar() {{
      // Sätter en body-klass när det finns osparade ändringar → CSS visar spara-knappar
      // inuti korten (bredvid prio-dropdown) samt den flytande baren i hörnet.
      const n = Object.keys(loadOverrides()).length;
      document.body.classList.toggle('has-pending-overrides', n > 0);
      const bar = document.getElementById('save-overrides-bar');
      if (bar) {{
        const count = bar.querySelector('.save-count');
        if (count) count.textContent = n;
        if (n > 0) bar.classList.add('active');
        else bar.classList.remove('active');
      }}
    }}

    // Spara direkt till GitHub Contents API. Sajten är privat → PAT kan finnas
    // i klienten. Ingen popup, ingen omladdning — bara ett litet toast-meddelande.
    async function saveToRepo() {{
      const o = loadOverrides();
      if (Object.keys(o).length === 0) return;
      if (!GITHUB_CONFIG.enabled) {{
        showSaveToast('⚠ GitHub-konfiguration saknas', true);
        return;
      }}
      const saveBtns = document.querySelectorAll('.card-save-btn, .save-overrides-btn');
      saveBtns.forEach(b => {{ b.disabled = true; b.textContent = '⏳ Sparar…'; }});
      try {{
        const api = `https://api.github.com/repos/${{GITHUB_CONFIG.repo}}/contents/.agent_overrides.json`;
        const authHeaders = {{
          'Authorization': `Bearer ${{GITHUB_CONFIG.pat}}`,
          'Accept': 'application/vnd.github+json',
        }};
        // Hämta ev. befintlig fil för SHA (behövs vid uppdatering)
        let sha = null;
        let existing = {{}};
        const getResp = await fetch(api, {{ headers: authHeaders }});
        if (getResp.status === 200) {{
          const meta = await getResp.json();
          sha = meta.sha;
          try {{
            existing = JSON.parse(atob(meta.content.replace(/\\s/g, '')));
          }} catch(e) {{ existing = {{}}; }}
        }} else if (getResp.status !== 404) {{
          throw new Error(`GET misslyckades: HTTP ${{getResp.status}}`);
        }}
        // Slå ihop lokala ändringar över befintliga
        const merged = Object.assign({{}}, existing, o);
        const newContent = JSON.stringify(merged, null, 2) + '\\n';
        const b64Content = btoa(unescape(encodeURIComponent(newContent)));
        const payload = {{
          message: `Prio-ändringar från Live-vyn (${{Object.keys(o).length}} nya)`,
          content: b64Content,
        }};
        if (sha) payload.sha = sha;
        const putResp = await fetch(api, {{
          method: 'PUT',
          headers: {{ ...authHeaders, 'Content-Type': 'application/json' }},
          body: JSON.stringify(payload),
        }});
        if (!putResp.ok) {{
          throw new Error(`Spara misslyckades: HTTP ${{putResp.status}}`);
        }}
        // Lyckades! Rensa lokal cache + återställ knapparna
        localStorage.removeItem(OVERRIDES_KEY);
        document.body.classList.remove('has-pending-overrides');
        document.querySelectorAll('.card.manually-set').forEach(c =>
          c.classList.remove('manually-set')
        );
        showSaveToast(`✓ Sparade ${{Object.keys(o).length}} ändringar`);
      }} catch (e) {{
        showSaveToast(`⚠ ${{e.message}}`, true);
      }} finally {{
        saveBtns.forEach(b => {{ b.disabled = false; b.textContent = '💾 Spara'; }});
      }}
    }}

    function showSaveToast(msg, isError) {{
      const toast = document.createElement('div');
      toast.className = 'save-toast' + (isError ? ' save-toast-error' : '');
      toast.textContent = msg;
      document.body.appendChild(toast);
      setTimeout(() => toast.classList.add('show'), 10);
      setTimeout(() => {{
        toast.classList.remove('show');
        setTimeout(() => toast.remove(), 300);
      }}, isError ? 4000 : 2200);
    }}

    function showOverridesModal() {{
      const o = loadOverrides();
      const json = JSON.stringify(o, null, 2);
      const modal = document.getElementById('overrides-modal');
      modal.querySelector('pre').textContent = json;
      modal.classList.add('active');
    }}
    function closeOverridesModal() {{
      document.getElementById('overrides-modal').classList.remove('active');
    }}
    function copyOverridesJson() {{
      const json = document.getElementById('overrides-modal').querySelector('pre').textContent;
      navigator.clipboard.writeText(json).then(() => {{
        const btn = document.getElementById('overrides-modal').querySelector('button.copy');
        const orig = btn.textContent;
        btn.textContent = "✓ Kopierat!";
        setTimeout(() => {{ btn.textContent = orig; }}, 1500);
      }});
    }}
    function clearAllOverrides() {{
      if (!confirm("Är du säker? Detta nollställer alla manuella prioritet-ändringar i webbläsaren (de som redan är i .agent_overrides.json behålls).")) return;
      localStorage.removeItem(OVERRIDES_KEY);
      location.reload();
    }}

    applyOverridesOnLoad();
    // Diagnostisk toast om sparning inte kommer fungera — så användaren vet direkt
    if (!GITHUB_CONFIG.enabled) {{
      setTimeout(() => showSaveToast(
        '⚠ Sparning inaktiverad: ' + (!GITHUB_CONFIG.pat ? 'PAT saknas' : 'repo saknas'),
        true
      ), 500);
    }}

    // Toggla "Visa mer" per tema på dashboarden
    function toggleDashMore(btn) {{
      const tema = btn.closest('.dash-tema');
      if (!tema) return;
      const isExpanded = tema.classList.toggle('expanded');
      const hiddenCount = tema.querySelectorAll('.dash-row.dash-hidden-extra').length;
      btn.textContent = isExpanded
        ? 'Visa färre ↑'
        : `Visa ${{hiddenCount}} till →`;
    }}

    // Klick på dashboard-rad (eller annan sammanfattning) → scrolla till fullkortet
    // och highlighta det i en sekund så användaren ser var det landade
    function jumpToCard(anchor, ev) {{
      // Om användaren klickade på en länk (typ dokumentet), låt den öppna som vanligt
      if (ev && ev.target && ev.target.tagName === 'A') return;
      const card = document.getElementById('card-' + anchor);
      if (!card) return;
      card.scrollIntoView({{ behavior: 'smooth', block: 'center' }});
      card.classList.add('flash-highlight');
      setTimeout(() => card.classList.remove('flash-highlight'), 1500);
    }}
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


def _is_english_title(title: str) -> bool:
    """Heuristik: är titeln på engelska? Räknar vanliga engelska stoppord.
    3+ stoppord = engelska."""
    if not title:
        return False
    EN_STOPWORDS = {
        " the ", " of ", " on ", " and ", " is ", " to ", " in ",
        " for ", " with ", " by ", " a ", " an ", " are ", " be ",
        " from ", " at ", " as ", " not ", " or ", " our ", " their ",
        " its ", " new ", " how ", " why ", " what ", " when ",
        " should ", " could ", " would ", " has ", " have ",
    }
    padded = f" {title.lower()} "
    hits = sum(1 for w in EN_STOPWORDS if w in padded)
    # Svenska-disqualifier: om titeln innehåller å/ä/ö är den nästan säkert svensk
    if any(c in title for c in "åäöÅÄÖ"):
        return False
    return hits >= 3


def _clean_title(item: dict) -> str:
    """Tvättar rubriker för tech-journalist: kort, konkret, kontext vid första anblick.
    - Slänger boilerplate-prefix ('Latest news', 'Next Committee Meeting', 'Committee on')
    - Översätter engelska titlar via sammanfattning
    - Prefixar med ärendenamn om rubriken saknar kontext
    - Trunkerar långa meningar vid naturliga brytpunkter (kolon, tankstreck, kommatecken)"""
    import re as _re
    title = (item.get("title") or "Utan titel").strip()
    analysis = item.get("analysis", {})
    # AI-genererad svensk rubrik har högsta prio om den finns och är kort nog
    ai_rubrik = (analysis.get("svensk_rubrik") or "").strip()
    if ai_rubrik and 15 < len(ai_rubrik) <= 110:
        return ai_rubrik
    samm = (analysis.get("sammanfattning") or "").strip()
    vinkel = (analysis.get("tech_vinkel") or "").strip()
    arende = (analysis.get("arende") or "").strip()

    # Bredare opaque-mönster — inkluderar alla "Latest news"-varianter och "Committee on"
    opaque_prefixes = (
        "Latest news -", "Latest news:", "Latest News",
        "Highlights -", "Highlights:", "Newsletters",
        "Next Committee Meeting", "Next meeting",
        "Committee on ",
        "Meeting of", "Meeting on",
        "News from",
    )
    opaque_patterns = (
        "e-mail alert", "press release",
        "opinion ", "annual report ",
        "committee meeting", "next meeting",
    )

    lt = title.lower()
    is_opaque = (
        any(title.startswith(p) for p in opaque_prefixes)
        or any(p in lt for p in opaque_patterns)
        or len(title) < 15
    )
    is_english = _is_english_title(title)

    def _cut_at_natural_break(text: str, hard_max: int = 90) -> str:
        """Trunkera vid naturlig brytpunkt istället för mitt i en tanke."""
        text = text.strip().rstrip(".")
        if len(text) <= hard_max:
            return text
        for br in (" — ", " – ", ": ", "; ", ", "):
            idx = text.find(br, 40, hard_max + 20)
            if idx > 0:
                return text[:idx].rstrip(",-–—; ") + "…"
        return text[:hard_max].rsplit(" ", 1)[0] + "…"

    chosen = title
    if (is_opaque or is_english) and samm:
        first = _re.split(r"(?<=[.!?])\s+", samm, maxsplit=1)[0]
        first = _cut_at_natural_break(first, 100)
        if len(first) > 15:
            chosen = first

    # Långa rubriker (även svenska) trunkeras vid rimlig brytpunkt
    if len(chosen) > 100:
        chosen = _cut_at_natural_break(chosen, 100)

    # Prefixa ärendenamn om rubriken saknar konkret kontext
    context_lack_starts = (
        "den ", "det ", "denna ", "detta ", "de nya ", "en ny ", "ett nytt ",
        "den nya ", "det nya ", "förslaget ", "regeringen ", "beslutet ",
        "utredningen ", "propositionen ", "utskottet ", "mötet ", "rapporten ",
        "kommissionen ", "myndigheten ",
    )
    chosen_low = chosen.lower()
    lacks_context = (
        arende
        and arende.lower() not in chosen_low
        and any(chosen_low.startswith(p) for p in context_lack_starts)
    )
    if lacks_context:
        result = f"{arende}: {chosen}"
        return _cut_at_natural_break(result, 110) if len(result) > 110 else result

    return chosen


def _original_title_if_translated(item: dict) -> str:
    """Returnerar originaltiteln om _clean_title gav en svensk översättning.
    Tom sträng om titel inte ändrades — visas som meta-undertext i kortet."""
    original = (item.get("title") or "").strip()
    cleaned = _clean_title(item)
    if cleaned != original and original and not original.startswith(("Latest news", "Highlights")):
        return original
    return ""


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
    label = RELEVANCE_LABEL.get(relevans, relevans)
    # Råversioner används av full_data (JSON-escapas separat).
    # Visuella versioner HTML-escapas så RSS-titlar med <script> är säkra.
    title_raw = _clean_title(item)
    title = _esc(title_raw)
    url_raw = item.get("url", "")
    url = _safe_url(url_raw)  # filtrerar bort javascript:/data:-URL:er
    from urllib.parse import urlparse as _urlparse
    _url_specific = bool(url_raw and _urlparse(url_raw).path.strip("/"))
    sammanfattning_raw = analysis.get("sammanfattning", "")
    vinkel_raw = analysis.get("tech_vinkel", "")
    source = _esc(item.get("source", ""))
    item_type = _esc(item.get("type", ""))
    date_str = _esc(_format_item_date(item.get("date", "")))
    meta_bits = [b for b in (date_str, source, item_type) if b]
    meta = " · ".join(meta_bits)
    link = f'href="{url}"' if (_url_specific and url) else ""

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

    samm_short = _esc(_first_sentence(sammanfattning_raw))
    vinkel_short = _esc(_first_sentence(vinkel_raw))

    samm_html = f"<p class='mini-samm'>{samm_short}</p>" if samm_short else ""
    vinkel_html = f"<p class='mini-vinkel'><strong>Tech-vinkel:</strong> {vinkel_short}</p>" if vinkel_short else ""
    meta_html = f"<p class='mini-meta'>{meta}</p>" if meta else ""

    # Bädda in full text som HTML-attribut — låt klick expandera kortet.
    # Rådata används här (JSON-strängifieras + HTML-escapas) så att expandMini()
    # får riktig text. JS-läsning skyddas via attribut-escape.
    import json as _json
    import html as _htmllib
    full_data = _htmllib.escape(_json.dumps({
        "title": title_raw, "url": url_raw, "source": item.get("source", ""), "meta": meta,
        "sammanfattning": sammanfattning_raw, "tech_vinkel": vinkel_raw,
        "varfor": analysis.get("varfor_viktigt", ""),
        "eu_koppling": analysis.get("eu_koppling") or "",
    }, ensure_ascii=False), quote=True)

    # data-url + data-relevans behövs för att sidopanelens dropdown ska kunna spara
    url_attr = _esc(url_raw) if url_raw else ""
    return f"""
    <div class="mini-card mini-card-expandable" style="border-left:3px solid {color}" onclick="expandMini(this)" data-full="{full_data}" data-url="{url_attr}" data-relevans="{_esc(relevans)}">
      <div class="mini-card-head">
        <span class="mini-prio-emoji" style="background:{color}" title="Prioritet: {label}">{emoji}</span>
        <a {link} target="_blank" class="mini-title" onclick="event.stopPropagation()">{title}</a>
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
    # Escapea alla externt-kommande strängar för att skydda mot XSS via
    # RSS-titlar som kan innehålla <script>, & eller andra HTML-specialtecken
    title = _esc(_clean_title(item))
    date_str = _esc(_format_item_date(item.get("date", "")))
    committee = _esc(item.get("committee", ""))
    url = _safe_url(item.get("url", ""))
    sammanfattning = _esc(analysis.get("sammanfattning", ""))
    tech_vinkel = _esc(analysis.get("tech_vinkel", ""))
    varfor = _esc(analysis.get("varfor_viktigt", ""))
    eu_koppling = _esc(analysis.get("eu_koppling") or "")
    keywords = analysis.get("keywords", [])

    arende = analysis.get("arende") or ""
    learned_from = _esc(analysis.get("_learned_from") or "")
    manually_set = analysis.get("_manually_set")
    original_title = _original_title_if_translated(item)
    original_row = (
        f'<p class="original-title" title="Originalrubrik">📰 <em>{_esc(original_title)}</em></p>'
        if original_title else ""
    )
    keyword_tags = " ".join(
        f'<span class="tag clickable" onclick="filterCards(\'keyword\', this.dataset.val)" '
        f'data-val="{_esc(kw.lower())}">{_esc(kw)}</span>'
        for kw in keywords[:4]
    )
    learning_note = (
        f'<p class="learning-note">🎓 AI lärde sig: {learned_from}</p>'
        if learned_from and not manually_set else ""
    )
    eu_row = f'<p class="eu-link">🇪🇺 <strong>EU-koppling:</strong> {eu_koppling}</p>' if eu_koppling and eu_koppling != "null" else ""
    from urllib.parse import urlparse as _urlparse
    _url_specific = bool(url and _urlparse(url).path.strip("/"))
    url_row = f'<a class="read-more" href="{url}" target="_blank">Läs originaldokumentet →</a>' if _url_specific else ""
    meta = " · ".join(filter(None, [date_str, committee]))
    arende_chip = (
        f'<span class="arende-chip clickable" onclick="filterCards(\'arende\', this.dataset.val)" '
        f'data-val="{_esc(arende.lower())}" title="Klicka för att filtrera på ärendet">📁 {_esc(arende)}</span>'
        if arende else ""
    )
    # Lowercase keyword-set för data-attribut (CSS söker kommaseparerade lc-värden)
    kw_data = _esc(",".join(k.lower() for k in keywords[:8]))

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

    _card_anchor = _item_anchor(item)
    return f"""
    <div class="card" id="card-{_card_anchor}" data-url="{url}" data-relevans="{_esc(relevans)}" data-arende="{_esc(arende.lower())}" data-keywords="{kw_data}">
      <div class="card-header" style="border-left:4px solid {color}">
        <select class="prio-select" style="background:{color}"
                onchange="setPrio(this.closest('.card').dataset.url, this.value); this.blur();"
                title="Ändra prioritet">
          <option value="hög" {"selected" if relevans == "hög" else ""}>🔴 Hög prioritet</option>
          <option value="medel" {"selected" if relevans == "medel" else ""}>🟡 Medel</option>
          <option value="låg" {"selected" if relevans == "låg" else ""}>🟢 Låg</option>
          <option value="utesluten" {"selected" if relevans == "utesluten" else ""}>🚫 Uteslut från rapport</option>
        </select>
        {arende_chip}
        <h3>{title}</h3>
        <p class="meta">{meta}</p>
        {original_row}
      </div>
      <div class="card-body">
        {sammanfattning_html}
        {vinkel_html}
        {varfor_html}
        {eu_row}
        {learning_note}
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
    "AI och algoritmer": "🤖",
    "Cybersäkerhet": "🛡️",
    "Dataskydd och GDPR": "🔒",
    "Övervakning och biometri": "👁️",
    "Sociala medier och plattformar": "💬",
    "Barnskydd online (CSAM/Chat Control)": "🧒",
    "Telekom och nätverk": "📡",
    "Digital identitet och e-tjänster": "🪪",
    "Tech-suveränitet och halvledare": "🔬",
    "Satellit och rymdteknik": "🛰️",
    "Uppkopplade fordon": "🚗",
    "Deepfakes och desinformation": "🎭",
    "Övrigt tech": "⚙️",
}

TEMA_ORDER = list(TEMA_EMOJI.keys())

# Keyword-baserad omkategorisering — mappar gamla breda teman till nya specifika
# via keywords/titel/tech_vinkel. Ordning betyder något: tidiga regler vinner.
_TEMA_KEYWORD_RULES = [
    ("Barnskydd online (CSAM/Chat Control)", (
        "csam", "chat control", "barnsex", "child sexual", "message scanning",
        "derogation", "barnskydd online", "åldersgräns",
    )),
    ("Deepfakes och desinformation", (
        "deepfake", "syntetiska medier", "ai-genererade bilder", "desinformation",
        "disinformation", "syntetiskt innehåll", "manipulation",
    )),
    ("Övervakning och biometri", (
        "ansiktsigenkänning", "biometri", "biometrisk", "facial recognition",
        "biometric", "kamerabevakning", "övervakning", "dataavläsning",
        "signalspaning", "datalagring",
    )),
    ("AI och algoritmer", (
        "ai act", "artificial intelligence", "artificiell intelligens",
        "generativ ai", "chatbot", "språkmodell", "maskininlärning",
        "algoritm", "ai-system", "machine learning", "llm ",
    )),
    ("Cybersäkerhet", (
        "cybersäkerhet", "cybersecurity", "nis2", "dora", "cyberattack",
        "ransomware", "phishing", "kryptering", "encryption",
        "cyberförsvar", "cyberhot", "cyberbrott", "hotbild",
    )),
    ("Dataskydd och GDPR", (
        "gdpr", "dataskydd", "personuppgifter", "dpa", "integritet",
        "one-stop-shop", "edpb", "imy", "datainspektionen",
    )),
    ("Barnskydd online (CSAM/Chat Control)", (
        "children online", "online safety", "unga på nätet",
    )),
    ("Sociala medier och plattformar", (
        "dsa ", "dma ", "digital services act", "digital markets act",
        "plattform", "sociala medier", "social media", "innehållsmoderering",
        "content moderation", "tiktok", "meta ", "facebook", "instagram",
    )),
    ("Telekom och nätverk", (
        "5g", "6g", "bredband", "mobil täckning", "fiber", "berec",
        "telekom", "roaming", "nätverksinfrastruktur", "spektrum",
        "digital networks act", "elektronisk kommunikation",
    )),
    ("Digital identitet och e-tjänster", (
        "e-legitimation", "digital identitet", "eid", "eidas",
        "digital wallet", "e-tjänst", "digital tillgänglighet",
        "digital pkt", "digitalisering av",
    )),
    ("Tech-suveränitet och halvledare", (
        "halvledare", "semiconductor", "chips act", "chipsact",
        "tech-suveränitet", "digital autonomi", "teknologisk suveränitet",
        "tech sovereignty", "kritiska teknologier",
    )),
    ("Satellit och rymdteknik", (
        "satellit", "galileo", "gnss", "rymdteknik", "space",
        "euspa", "satellitnavigering", "osnma",
    )),
    ("Uppkopplade fordon", (
        "uppkopplade fordon", "connected vehicle", "v2x", "autonoma fordon",
        "self-driving", "elfordon", "laddinfrastruktur",
    )),
]

# Gamla tema-namn → nya (för items som fortfarande har gamla värden i cachen)
_LEGACY_TEMA_MAP = {
    "AI": "AI och algoritmer",
    "Dataskydd och integritet": "Dataskydd och GDPR",
    "Plattformsreglering": "Sociala medier och plattformar",
    "Sociala medier": "Sociala medier och plattformar",
    "Halvledare": "Tech-suveränitet och halvledare",
    "Digital infrastruktur": None,  # behöver keyword-omkategorisering
    "Övrigt tech": None,
}


def _refine_tema(item: dict) -> str:
    """Bestämmer tema för dashboard-vyn: kollar först keywords/titel för specifika
    mönster, sen legacy-mapp, sen fallback till Övrigt tech."""
    analysis = item.get("analysis") or {}
    raw_tema = analysis.get("tema") or ""
    keywords = " ".join(k.lower() for k in (analysis.get("keywords") or []))
    title = (item.get("title") or "").lower()
    vinkel = (analysis.get("tech_vinkel") or "").lower()
    haystack = f"{keywords} {title} {vinkel}"

    # Keyword-regler vinner alltid (mer specifikt än raw_tema)
    for new_tema, patterns in _TEMA_KEYWORD_RULES:
        if any(p in haystack for p in patterns):
            return new_tema

    # Fall tillbaka till mappning från gammalt tema
    if raw_tema in _LEGACY_TEMA_MAP:
        mapped = _LEGACY_TEMA_MAP[raw_tema]
        if mapped:
            return mapped

    # Om raw_tema redan är ett giltigt nytt tema, behåll det
    if raw_tema in TEMA_ORDER:
        return raw_tema

    return "Övrigt tech"

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
    """Bygger en 'Senaste 24h'-ruta: items vars faktiska publiceringsdatum är
    idag eller igår. Cachar_at duger inte som "nytt"-indikator: när en ny källa
    läggs till får alla dess historiska items cached_at=idag men publiceringsdatum
    är gammalt. Vi vill bara visa dokument som faktiskt är nya."""
    try:
        import memory as _mem
        cache = _mem.load_analysis_cache()
    except Exception:
        cache = {}

    yesterday_date = today_date - timedelta(days=1)
    # Bara högt prioriterade i "Nytt idag" — medel/låg hamnar i sina egna sektioner
    ok_relevans = {"hög"}
    new_items: list[dict] = []
    items_by_url = {i.get("url"): i for i in items if i.get("url")}
    for url, entry in cache.items():
        analysis = entry.get("analysis") or {}
        if analysis.get("relevans") not in ok_relevans:
            continue
        # Kolla publiceringsdatum — bara dokument från idag eller igår räknas som nya
        raw_date = entry.get("date") or ""
        # Om cachen saknar datum, fall tillbaka till memory-versionen
        if not raw_date:
            mem_item = items_by_url.get(url)
            if mem_item:
                raw_date = mem_item.get("date", "")
        parsed = _parse_date(raw_date)
        if parsed is None:
            continue  # inget giltigt datum — kan inte avgöra om det är nytt
        if parsed != today_date and parsed != yesterday_date:
            continue
        # Föredra den item-instans som finns i items-listan (har all metadata);
        # fall tillbaka till cache-entryn om itemet klustrats bort.
        # Cache-entryn innehåller numera även source/type/date/committee/summary
        # så att fallback-itemet får full meta (annars visades cached items utan källa).
        item = items_by_url.get(url) or {
            "title": entry.get("title", ""),
            "url": url,
            "source": entry.get("source", ""),
            "type": entry.get("type", ""),
            "date": entry.get("date", ""),
            "committee": entry.get("committee", ""),
            "summary": entry.get("summary", ""),
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
        item_date = _format_item_date(item.get("date", ""))
        date_html = f'<span class="nt-date">{_esc(item_date)}</span>' if item_date else ""
        rows += f"""
        <li onclick="expandMini(this)" data-full="{full_data}" data-url="{_esc(item.get('url',''))}" data-relevans="{_esc(relevans)}" class="nt-li-click">
          <span class="nt-dot" style="background:{dot_color}"></span>
          <span class="nt-link">{title}</span>
          {date_html}
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

    # Gruppera efter tema — använd _refine_tema för att bryta upp brett
    # kategoriserade items (t.ex. "Digital infrastruktur") i granulära ämnen
    by_tema: dict[str, list[dict]] = {}
    for item in items:
        tema = _refine_tema(item)
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
        for _idx, item in enumerate(tema_items):
            _is_hidden = _idx >= 3  # bara topp 3 syns som default per tema
            analysis = item.get("analysis", {})
            title = _clean_title(item)
            relevans = analysis.get("relevans", "okänd")
            dot_color = RELEVANCE_DOT.get(relevans, "#ccc")
            source = item.get("source", "")
            source_short = SOURCE_SHORT.get(source, source)

            # Badge för status + formaterat datum (alltid synligt).
            # Använd _parse_date för att hantera både ISO och RSS-format
            raw_date = item.get("date") or ""
            d = _parse_date(raw_date)
            status_badge = ""
            date_label = ""
            if d:
                date_label = _swedish_date(d)
                if d == today_date:
                    status_badge = '<span class="dash-badge dash-badge-today">IDAG</span>'
                elif d == today_date - timedelta(days=1):
                    status_badge = '<span class="dash-badge dash-badge-new">IGÅR</span>'
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
            # Bädda in all info i data-full — expandMini öppnar sidopanelen med kortet
            import json as _json
            import html as _htmllib
            _dash_meta_parts = [item.get("source", ""), item.get("committee", "")]
            _dash_meta = " · ".join([p for p in _dash_meta_parts if p])
            dash_full_data = _htmllib.escape(_json.dumps({
                "title": title,
                "url": item.get("url", ""),
                "source": item.get("source", ""),
                "meta": _dash_meta,
                "sammanfattning": sammanfattning,
                "tech_vinkel": vinkel,
                "varfor": analysis.get("varfor_viktigt", ""),
                "eu_koppling": analysis.get("eu_koppling") or "",
            }, ensure_ascii=False), quote=True)
            _hidden_class = " dash-hidden-extra" if _is_hidden else ""
            rows += f"""
            <div class="dash-row clickable{_hidden_class}" id="{anchor}" data-full="{dash_full_data}" data-url="{_esc(item.get('url',''))}" data-relevans="{_esc(relevans)}" onclick="expandMini(this)" title="Klicka för att öppna ärendekortet">
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

        _extra_count = max(0, len(tema_items) - 3)
        _more_btn = (
            f'<button class="dash-more-btn" onclick="toggleDashMore(this)">'
            f'Visa {_extra_count} till →</button>'
            if _extra_count > 0 else ""
        )
        blocks += f"""
        <div class="dash-tema">
          <h3 class="dash-tema-title">{emoji} {tema} <span class="dash-count">{len(tema_items)}</span></h3>
          <div class="dash-rows">{rows}</div>
          {_more_btn}
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
            # Använd _format_item_date som klarar både ISO och RSS-format
            # (förut visades "Wed, 15 Ap" för ~56% av dokumenten pga rå [:10]-slicing)
            doc_date = _format_item_date(doc.get("date", ""))
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
             important_dates: Optional[dict] = None,
             include_header: bool = True,
             github_pat: str = "",
             github_repo: str = "") -> str:
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
<!-- Content Security Policy: andra säkerhetslagret utöver HTML-escape.
     Webbläsaren tvingas följa dessa regler även om någon lyckas smyga in
     skadlig kod via en RSS-titel. -->
<meta http-equiv="Content-Security-Policy" content="
  default-src 'self';
  script-src 'unsafe-inline';
  style-src 'self' 'unsafe-inline';
  img-src 'self' data: https:;
  font-src 'self' data:;
  connect-src 'none';
  object-src 'none';
  frame-ancestors 'self';
  base-uri 'self';
  form-action 'none';
">
<meta http-equiv="X-Content-Type-Options" content="nosniff">
<meta name="referrer" content="strict-origin-when-cross-origin">
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
  .stat.clickable:hover {{ background: rgba(255,255,255,0.25); }}
  /* Kompakt stats-rad när live-vyn körs utan egen header (Streamlit-topbaren tar rubriken) */
  .compact-stats {{
    display: flex; gap: 0.75rem; padding: 0.5rem 1rem 0;
    justify-content: flex-end; flex-wrap: wrap;
  }}
  .compact-stats .stat {{ background: #eef2ff; color: #1a1a2e; }}
  .compact-stats .stat.clickable:hover {{ background: #dbe4ff; }}
  main {{ max-width: 1400px; margin: 1rem auto 2rem; padding: 0 1.5rem 4rem; }}

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
  .nt-date {{ font-size: 0.78rem; color: #667; margin-right: 0.5rem; white-space: nowrap; }}
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
  /* Gömda extra items per tema — visas när "Visa X till" klickas */
  .dash-row.dash-hidden-extra {{ display: none; }}
  .dash-tema.expanded .dash-row.dash-hidden-extra {{ display: grid; }}
  .dash-more-btn {{
    background: none; border: 0; color: #4a6cf7;
    padding: 0.5rem 0.25rem 0; font-size: 0.85rem;
    cursor: pointer; text-align: left; width: 100%;
    font-weight: 500;
  }}
  .dash-more-btn:hover {{ text-decoration: underline; }}
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
  .original-title {{
    font-size: 0.78rem; color: #666;
    margin-top: 0.15rem; padding: 0.2rem 0;
    border-top: 1px dashed #e0e0e0;
  }}
  .original-title em {{ font-style: italic; }}
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
  .learning-note {{
    font-size: 0.78rem; color: #6b21a8;
    background: #faf5ff; border-left: 3px solid #c084fc;
    padding: 0.4rem 0.7rem; border-radius: 4px;
    margin-top: 0.5rem;
  }}
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
  .arende-chip {{
    display: inline-block;
    background: #fef3c7;
    color: #92400e;
    font-size: 0.72rem;
    font-weight: 600;
    padding: 0.18rem 0.65rem;
    border-radius: 20px;
    margin-left: 0.4rem;
    vertical-align: middle;
  }}
  .clickable {{ cursor: pointer; user-select: none; transition: transform 0.08s, box-shadow 0.08s; }}
  .clickable:hover {{ transform: translateY(-1px); box-shadow: 0 2px 6px rgba(0,0,0,0.18); }}
  .clickable:active {{ transform: translateY(0); }}
  .filter-bar {{
    position: sticky; top: 0; z-index: 100;
    background: linear-gradient(90deg, #4a6cf7, #6366f1);
    color: #fff; padding: 0.75rem 1.25rem;
    border-radius: 10px; margin: 1rem 0;
    display: none; align-items: center; gap: 1rem;
    box-shadow: 0 4px 14px rgba(74,108,247,0.35);
  }}
  .filter-bar.active {{ display: flex; }}
  .filter-bar-label {{ flex: 1; font-weight: 500; font-size: 0.95rem; }}
  .filter-bar-value {{ background: rgba(255,255,255,0.25); padding: 0.2rem 0.7rem; border-radius: 12px; font-weight: 700; }}
  .filter-reset-btn {{
    background: #fff; color: #4a6cf7; border: 0;
    padding: 0.45rem 1rem; border-radius: 8px; font-weight: 600;
    cursor: pointer; font-size: 0.9rem;
  }}
  .filter-reset-btn:hover {{ background: #f0f4ff; }}
  .card.filtered-out {{ display: none !important; }}
  .prio-trigger, .mini-prio-trigger {{
    cursor: pointer; user-select: none;
    transition: transform 0.08s, box-shadow 0.08s;
  }}
  .prio-trigger:hover, .mini-prio-trigger:hover {{
    transform: translateY(-1px); box-shadow: 0 2px 6px rgba(0,0,0,0.18);
  }}
  .mini-prio-trigger {{
    display: inline-flex; align-items: center; justify-content: center;
    width: 26px; height: 26px; border-radius: 50%;
    font-size: 0.85rem; flex-shrink: 0;
    color: white;
  }}
  .mini-card-head {{ display: flex; align-items: center; gap: 0.5rem; }}
  .mini-card .mini-title {{ flex: 1; min-width: 0; }}
  /* Dropdown-meny för prio-val */
  /* Native <select> som prio-väljare — funkar oavsett iframe/CSS-quirks */
  .prio-select, .mini-prio-select {{
    color: white; border: 0;
    font-size: 0.75rem; font-weight: 600;
    padding: 0.2rem 0.5rem; border-radius: 20px;
    cursor: pointer; margin-left: 0.4rem;
    -webkit-appearance: none; appearance: none;
    text-align: center;
  }}
  .prio-select {{ font-size: 0.72rem; padding: 0.2rem 0.6rem; }}
  /* Spara-knapp bredvid prio-dropdown — bara synlig när ändringar finns */
  .card-save-btn {{
    display: none;
    background: #8b5cf6; color: white; border: 0;
    font-size: 0.72rem; font-weight: 600;
    padding: 0.25rem 0.65rem; border-radius: 20px;
    cursor: pointer; margin-left: 0.4rem;
    vertical-align: middle;
  }}
  .card-save-btn:hover {{ background: #7c3aed; }}
  /* Visas när sidan har pending overrides */
  body.has-pending-overrides .card-save-btn {{ display: inline-block; }}
  /* Litet toast-meddelande i nedre hörnet vid save */
  .save-toast {{
    position: fixed; bottom: 2rem; right: 2rem;
    background: #10b981; color: white;
    padding: 0.8rem 1.2rem; border-radius: 8px;
    font-weight: 600; font-size: 0.9rem;
    box-shadow: 0 6px 20px rgba(16,185,129,0.35);
    z-index: 3000;
    opacity: 0; transform: translateY(20px);
    transition: opacity 0.3s, transform 0.3s;
  }}
  .save-toast.show {{ opacity: 1; transform: translateY(0); }}
  .save-toast-error {{
    background: #ef4444;
    box-shadow: 0 6px 20px rgba(239,68,68,0.35);
  }}
  .mini-prio-select {{
    width: 26px; height: 26px; padding: 0;
    border-radius: 50%; font-size: 0.75rem;
  }}
  /* Enkel prioritet-indikator som ren emoji-cirkel (ingen dropdown i mini-vyn) */
  .mini-prio-emoji {{
    display: inline-flex; align-items: center; justify-content: center;
    width: 26px; height: 26px; border-radius: 50%;
    font-size: 0.9rem; flex-shrink: 0;
    line-height: 1;
  }}
  /* Klick-flash-highlight på fullkortet när man hoppar från dashboarden */
  .card.flash-highlight {{
    animation: flashHighlight 1.5s ease;
  }}
  @keyframes flashHighlight {{
    0% {{ box-shadow: 0 0 0 3px rgba(74,108,247,0); background: #fff; }}
    30% {{ box-shadow: 0 0 0 3px rgba(74,108,247,0.6); background: #f0f4ff; }}
    100% {{ box-shadow: 0 0 0 3px rgba(74,108,247,0); background: #fff; }}
  }}
  .prio-select option, .mini-prio-select option {{
    background: white; color: #1a1a2e;
    padding: 0.4rem;
  }}
  .prio-menu {{
    position: fixed; z-index: 2000;
    background: #fff; border: 1px solid #ddd;
    border-radius: 10px; box-shadow: 0 8px 24px rgba(0,0,0,0.25);
    padding: 0.35rem; min-width: 200px;
    font-size: 0.9rem;
  }}
  .prio-menu-item {{
    display: flex; align-items: center; gap: 0.6rem;
    padding: 0.5rem 0.7rem; border-radius: 6px;
    cursor: pointer; color: #1a1a2e;
  }}
  .prio-menu-item:hover {{ background: #f0f4ff; }}
  .prio-menu-item.current {{ background: #eef2ff; font-weight: 600; }}
  .prio-menu-item .dot {{
    width: 10px; height: 10px; border-radius: 50%; flex-shrink: 0;
  }}
  .prio-menu-item.exclude {{
    color: #666; border-top: 1px solid #eee;
    margin-top: 0.25rem; padding-top: 0.6rem;
  }}
  .prio-menu-item.exclude:hover {{ background: #fef2f2; color: #991b1b; }}
  /* Uteslutna kort göms */
  .card.excluded, .mini-card.excluded, .dash-row.excluded, .panel-card.excluded {{ display: none !important; }}
  /* Nytt idag-items som ändrats till lägre prio göms från listan */
  .nt-li-click.nt-hidden-by-prio {{ display: none !important; }}
  /* Flärp uppe som visar utslutna items */
  .excluded-banner {{
    position: sticky; top: 0; z-index: 90;
    background: #f3f4f6; color: #4b5563;
    padding: 0.55rem 1rem; border-radius: 8px;
    margin: 0.5rem 0; display: none;
    align-items: center; gap: 0.75rem;
    font-size: 0.88rem; border: 1px solid #e5e7eb;
  }}
  .excluded-banner.active {{ display: flex; }}
  .excluded-banner .count {{ font-weight: 700; color: #374151; }}
  .excluded-btn {{
    background: transparent; border: 1px solid #d1d5db;
    color: #4b5563; padding: 0.3rem 0.75rem; border-radius: 6px;
    cursor: pointer; font-size: 0.85rem;
  }}
  .excluded-btn:hover {{ background: #fff; color: #1a1a2e; }}
  /* När uteslutna visas: markera dem visuellt */
  .card.excluded.showing, .mini-card.excluded.showing,
  .dash-row.excluded.showing, .panel-card.excluded.showing {{
    display: block !important; opacity: 0.55;
    outline: 2px dashed #d1d5db;
  }}
  .card.manually-set {{ box-shadow: 0 0 0 2px #8b5cf6, 0 2px 8px rgba(139,92,246,0.2); }}
  .card.manually-set .relevance-badge::after {{
    content: " ✏️"; font-size: 0.7rem;
  }}
  .save-overrides-bar {{
    position: fixed; bottom: 1rem; right: 1rem; z-index: 200;
    background: #8b5cf6; color: #fff;
    padding: 1rem 1.4rem; border-radius: 12px;
    display: none; align-items: center; gap: 1rem;
    box-shadow: 0 6px 20px rgba(139,92,246,0.4);
    max-width: 90vw;
  }}
  .save-overrides-bar.active {{ display: flex; }}
  .save-overrides-btn {{
    background: #fff; color: #8b5cf6; border: 0;
    padding: 0.5rem 1.1rem; border-radius: 8px;
    font-weight: 700; cursor: pointer; font-size: 0.9rem;
  }}
  .save-overrides-btn:hover {{ background: #f3f0ff; }}
  .overrides-modal {{
    position: fixed; top: 0; left: 0; right: 0; bottom: 0;
    background: rgba(0,0,0,0.7); z-index: 1000;
    display: none; align-items: center; justify-content: center;
    padding: 2rem;
  }}
  .overrides-modal.active {{ display: flex; }}
  .overrides-modal-inner {{
    background: #fff; border-radius: 12px;
    padding: 2rem; max-width: 600px; width: 100%;
    max-height: 90vh; overflow-y: auto;
  }}
  .overrides-modal h3 {{ margin-top: 0; }}
  .overrides-modal pre {{
    background: #f5f5fa; border: 1px solid #ddd;
    padding: 1rem; border-radius: 8px;
    overflow-x: auto; font-size: 0.8rem;
    max-height: 200px;
  }}
  .overrides-modal ol {{ line-height: 1.7; font-size: 0.92rem; }}
  .overrides-modal code {{
    background: #f0f0f5; padding: 0.1rem 0.4rem;
    border-radius: 4px; font-size: 0.85rem;
  }}
  .overrides-modal button.close {{
    background: #ddd; color: #333; border: 0;
    padding: 0.5rem 1rem; border-radius: 6px;
    cursor: pointer; float: right; margin-top: 1rem;
  }}
  .overrides-modal button.copy {{
    background: #8b5cf6; color: #fff; border: 0;
    padding: 0.5rem 1rem; border-radius: 6px;
    cursor: pointer; margin-top: 0.5rem;
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
{'''<header>
  <h1>🔍 Tech Vinklar</h1>
  <p>EU &amp; Riksdagen · ''' + date_str + '''</p>
  <div class="stats">
    <div class="stat clickable" onclick="resetFilter()" title="Visa alla ärenden">📋 ''' + str(len(items)) + ''' ärenden</div>
    <div class="stat clickable" onclick="filterCards(\'relevans\', \'hög\')" title="Filtrera på hög prioritet">🔴 ''' + str(n_hog) + ''' hög prioritet</div>
    <div class="stat clickable" onclick="filterCards(\'relevans\', \'medel\')" title="Filtrera på medel prioritet">🟡 ''' + str(n_med) + ''' medel</div>
  </div>
</header>''' if include_header else '<div class="compact-stats"><span class="stat clickable" onclick="resetFilter()" title="Visa alla">📋 ' + str(len(items)) + '</span><span class="stat clickable" onclick="filterCards(\'relevans\', \'hög\')" title="Filtrera hög">🔴 ' + str(n_hog) + '</span><span class="stat clickable" onclick="filterCards(\'relevans\', \'medel\')" title="Filtrera medel">🟡 ' + str(n_med) + '</span></div>'}
<main>
  <div id="filter-bar" class="filter-bar">
    <span class="filter-bar-label"></span>
    <span class="filter-bar-value"></span>
    <button class="filter-reset-btn" onclick="resetFilter()">✕ Återställ</button>
  </div>
  <div id="excluded-banner" class="excluded-banner">
    <span>🚫 <span class="count">0</span> ärenden uteslutna ur rapporten.</span>
    <button id="excluded-toggle-btn" class="excluded-btn" onclick="toggleExcludedVisible()">Visa dem</button>
  </div>

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

    # Injicera GitHub-konfiguration för direkt-spara från klient-JS.
    # Använd JSON-dump så tokens/repo-namn escapas säkert och inte kan bryta ut ur JS.
    import json as _json_inj
    _gh_config = _json_inj.dumps({
        "pat": github_pat or "",
        "repo": github_repo or "",
        "enabled": bool(github_pat and github_repo),
    })
    # Ersätt </ med <\/ så JSON inte kan stänga en </script>-tagg tidigt
    _gh_config = _gh_config.replace("</", "<\\/")
    html = html.replace("__GITHUB_CONFIG_PLACEHOLDER__", _gh_config)

    with open(output_path, "w", encoding="utf-8") as f:
        f.write(html)

    return output_path
