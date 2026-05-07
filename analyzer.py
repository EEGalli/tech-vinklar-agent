"""
Analyserar insamlade politiska ärenden med AI för att identifiera tech-vinklar.

Stödjer två backends:
- Google Gemini API (cloud, snabb) — används om GEMINI_API_KEY finns i miljö
- Ollama (lokalt) — fallback när ingen API-nyckel finns

Nyckeln kan komma från:
- Miljövariabel GEMINI_API_KEY
- Streamlit secrets (st.secrets["GEMINI_API_KEY"])
- Filen .env i projektroten
"""
import json
import os
import re
import requests

OLLAMA_URL = "http://localhost:11434/api/generate"
DEFAULT_MODEL = "gemma3:12b"  # Använd för Ollama; för Gemini sätts namnet i _call_gemini

GEMINI_MODEL = "gemini-1.5-flash"  # Snabb, gratis tier 1500/dag


def _load_env_file() -> None:
    """Enkel .env-läsare — laddar GEMINI_API_KEY etc. om filen finns."""
    env_path = os.path.join(os.path.dirname(__file__), ".env")
    if not os.path.exists(env_path):
        return
    try:
        with open(env_path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                if "=" in line:
                    key, _, value = line.partition("=")
                    key = key.strip()
                    value = value.strip().strip('"').strip("'")
                    if key and value and key not in os.environ:
                        os.environ[key] = value
    except Exception:
        pass


_load_env_file()


def _get_gemini_key() -> str:
    """Hämtar Gemini API-nyckel från miljö eller Streamlit secrets."""
    key = os.environ.get("GEMINI_API_KEY", "")
    if key and key != "KLISTRA_IN_DIN_NYCKEL_HÄR":
        return key
    # Fallback: Streamlit secrets (om appen körs i Streamlit-context)
    try:
        import streamlit as st
        return st.secrets.get("GEMINI_API_KEY", "")
    except Exception:
        return ""


def _use_gemini() -> bool:
    return bool(_get_gemini_key())


# Mappning från förkortning → svensk förklaring.
# Körs som efterbehandling på AI-output så journalisten slipper gissa
# vad IMCO, DSA, NIS2 osv. betyder. Ordning: längsta först för att undvika
# att t.ex. "DORA" ersätts i "DORA-förordningen".
ABBREV_EXPANSIONS = [
    # ── EP-utskott med "-utskottet"-suffix (hanteras först för att undvika dubbelexpansion)
    (r"\bIMCO-utskottet\b", "EP-utskottet för inre marknaden och konsumentskydd"),
    (r"\bITRE-utskottet\b", "EP-utskottet för industri, forskning och energi"),
    (r"\bLIBE-utskottet\b", "EP-utskottet för medborgerliga fri- och rättigheter"),
    (r"\bAFET-utskottet\b", "EP:s utrikesutskott"),
    (r"\bECON-utskottet\b", "EP:s ekonomiutskott"),
    (r"\bENVI-utskottet\b", "EP:s miljöutskott"),
    (r"\bTRAN-utskottet\b", "EP-utskottet för transport och turism"),
    (r"\bJURI-utskottet\b", "EP:s rättsliga utskott"),
    (r"\bCULT-utskottet\b", "EP-utskottet för kultur och utbildning"),
    (r"\bFEMM-utskottet\b", "EP:s jämställdhetsutskott"),
    (r"\bAIDA-utskottet\b", "EP:s särskilda utskott om artificiell intelligens"),
    (r"\bEMPL-utskottet\b", "EP-utskottet för sysselsättning och sociala frågor"),
    (r"\bBUDG-utskottet\b", "EP:s budgetutskott"),
    (r"\bAGRI-utskottet\b", "EP:s jordbruksutskott"),
    (r"\bREGI-utskottet\b", "EP-utskottet för regional utveckling"),
    (r"\bPECH-utskottet\b", "EP:s fiskeutskott"),
    (r"\bPETI-utskottet\b", "EP-utskottet för framställningar"),
    (r"\bSANT-utskottet\b", "EP-utskottet för folkhälsa"),
    (r"\bDEVE-utskottet\b", "EP:s utvecklingsutskott"),
    (r"\bINTA-utskottet\b", "EP-utskottet för internationell handel"),
    (r"\bFISC-utskottet\b", "EP:s underutskott för skattefrågor"),
    (r"\bSEDE-utskottet\b", "EP:s underutskott för säkerhet och försvar"),
    (r"\bDROI-utskottet\b", "EP:s underutskott för mänskliga rättigheter"),
    # ── EP-utskott utan suffix
    (r"\bIMCO\b", "EP-utskottet för inre marknaden (IMCO)"),
    (r"\bITRE\b", "EP-utskottet för industri och forskning (ITRE)"),
    (r"\bLIBE\b", "EP:s utskott för fri- och rättigheter (LIBE)"),
    (r"\bAFET\b", "EP:s utrikesutskott (AFET)"),
    (r"\bECON\b", "EP:s ekonomiutskott (ECON)"),
    (r"\bENVI\b", "EP:s miljöutskott (ENVI)"),
    (r"\bTRAN\b", "EP-utskottet för transport (TRAN)"),
    (r"\bJURI\b", "EP:s rättsliga utskott (JURI)"),
    (r"\bCULT\b", "EP-utskottet för kultur och utbildning (CULT)"),
    (r"\bFEMM\b", "EP:s jämställdhetsutskott (FEMM)"),
    (r"\bAIDA\b", "EP:s särskilda utskott om AI (AIDA)"),
    (r"\bEMPL\b", "EP-utskottet för sysselsättning (EMPL)"),
    (r"\bBUDG\b", "EP:s budgetutskott (BUDG)"),
    (r"\bAGRI\b", "EP:s jordbruksutskott (AGRI)"),
    (r"\bREGI\b", "EP-utskottet för regional utveckling (REGI)"),
    (r"\bPECH\b", "EP:s fiskeutskott (PECH)"),
    (r"\bSANT\b", "EP-utskottet för folkhälsa (SANT)"),
    # ── Riksdagens utskott
    (r"\bTU-utskottet\b", "trafikutskottet"),
    (r"\bJuU-utskottet\b", "justitieutskottet"),
    (r"\bFöU-utskottet\b", "försvarsutskottet"),
    (r"\bCU-utskottet\b", "civilutskottet"),
    (r"\bUbU-utskottet\b", "utbildningsutskottet"),
    (r"\bFiU-utskottet\b", "finansutskottet"),
    (r"\bSkU-utskottet\b", "skatteutskottet"),
    (r"\bNU-utskottet\b", "näringsutskottet"),
    (r"\bKU-utskottet\b", "konstitutionsutskottet"),
    (r"\bSoU-utskottet\b", "socialutskottet"),
    (r"\bMJU-utskottet\b", "miljö- och jordbruksutskottet"),
    (r"\bAU-utskottet\b", "arbetsmarknadsutskottet"),
    (r"\bUU-utskottet\b", "utrikesutskottet"),
    # EU-lagar och förordningar
    (r"\bAI Act\b", "AI Act (EU:s AI-förordning)"),
    (r"\bDigital Services Act\b", "Digital Services Act (EU-lagen som tvingar plattformar att hantera olagligt innehåll)"),
    (r"\bDigital Markets Act\b", "Digital Markets Act (EU-lagen som reglerar stora plattformars marknadsmakt)"),
    (r"\bDSA\b", "DSA (EU-lagen som tvingar plattformar att hantera olagligt innehåll)"),
    (r"\bDMA\b", "DMA (EU-lagen som reglerar stora plattformars marknadsmakt)"),
    (r"\bNIS2\b", "NIS2 (EU:s cybersäkerhetsdirektiv för kritiska verksamheter)"),
    (r"\bDORA\b", "DORA (EU-förordningen om digital motståndskraft i finanssektorn)"),
    (r"\bCRA\b", "CRA (Cyber Resilience Act — EU-lag om cybersäkerhet i produkter)"),
    (r"\beIDAS\b", "eIDAS (EU:s förordning om digital ID)"),
    (r"\bGDPR\b", "GDPR (EU:s dataskyddsförordning)"),
    (r"\bData Act\b", "Data Act (EU-lagen om delning av industridata)"),
    (r"\bChips Act\b", "Chips Act (EU-satsningen på halvledartillverkning)"),
    # Svenska myndigheter
    (r"\bNCSC\b", "det nationella cybersäkerhetscentret (NCSC)"),
    (r"\bMSB\b", "Myndigheten för samhällsskydd och beredskap (MSB)"),
    (r"\bPTS\b", "Post- och telestyrelsen (PTS)"),
    (r"\bFRA\b", "Försvarets radioanstalt (FRA)"),
    # EU-institutioner
    (r"\bEP\b(?!-)", "EU-parlamentet"),
]

_ABBREV_COMPILED = [(re.compile(pat), repl) for pat, repl in ABBREV_EXPANSIONS]


def _expand_abbreviations(text: str) -> str:
    """Byter ut förkortningar mot svenska förklaringar.
    - Expanderar bara FÖRSTA förekomsten per text
    - Skippar förekomster som redan står INNE i en parentes
      (dvs. text som kommer från en tidigare expansion)
    - Skippar om det fullständiga namnet redan står precis före förkortningen
      (t.ex. "Digital Services Act (DSA)" — då expanderar vi inte DSA igen)"""
    if not text:
        return text
    seen: set[str] = set()
    for pattern, replacement in _ABBREV_COMPILED:
        if pattern.pattern in seen:
            continue
        # Smart sub: skippa i två fall
        # 1. Inuti en parentes (text från tidigare expansion)
        # 2. En parentes följer direkt efter — AI har redan skrivit förklaring
        def _smart_sub(match):
            start = match.start()
            end = match.end()
            before = text[:start]
            last_open = before.rfind("(")
            last_close = before.rfind(")")
            if last_open > last_close:
                return match.group(0)  # inuti parentes
            # Kolla om parentes kommer direkt efter (med ev. mellanslag)
            after = text[end:end + 3].lstrip()
            if after.startswith("("):
                return match.group(0)  # AI har redan förklarat
            return replacement
        new_text, count = pattern.subn(_smart_sub, text, count=1)
        if count > 0:
            seen.add(pattern.pattern)
            text = new_text
    return text


def _build_system_prompt() -> str:
    """Bygger systemprompt med aktuell ämneslista från topics.md."""
    try:
        from config import HIGH_PRIORITY_TOPICS, LOWER_PRIORITY_TOPICS, EXCLUDE_TOPICS
        high_str = ", ".join(HIGH_PRIORITY_TOPICS[:12]) if HIGH_PRIORITY_TOPICS else "AI, cybersäkerhet, sociala medier"
        low_str = ", ".join(LOWER_PRIORITY_TOPICS[:8]) if LOWER_PRIORITY_TOPICS else "rymden, energi"
        excl_str = ", ".join(EXCLUDE_TOPICS[:6]) if EXCLUDE_TOPICS else "inga"
    except Exception:
        high_str = "AI, cybersäkerhet, sociala medier, deepfakes, 5G"
        low_str = "rymden, energi, fordon"
        excl_str = "inga"

    return f"""Du är en expert på tech-politik som hjälper en journalist att identifiera tech-vinklar i politiska ärenden från EU och Riksdagen.

Prioriteringslista (uppdateras av journalisten):
- HÖG prioritet: {high_str}
- LÄGRE prioritet: {low_str}
- EXKLUDERA (sätt relevans=låg): {excl_str}

═══ RELEVANSREGLER (STRIKT!) ═══
Ett ärende är tech-relevant BARA om du kan peka på NÅGOT KONKRET:
- En namngiven lag eller strategi (AI Act, NIS2, cybersäkerhetscenter, Chips Act)
- En namngiven satsning eller myndighetsåtgärd inom tech (t.ex. "500 mkr till AI-forskning")
- Ett konkret beslut som direkt reglerar tech (t.ex. "förbud mot ansiktsigenkänning på allmän plats")

Om du bara kan säga "budgeten KAN innehålla digitala satsningar" eller "propositionen KAN påverka tech" eller "även om det primärt handlar om X, finns en indirekt koppling till tech" eller "diskussionen kommer SANNOLIKT att beröra tech" — då är det INTE tech-relevant. Sätt relevans="låg".

Budgetpropositioner och vårpropositioner är LÅG om de inte nämner ett specifikt tech-initiativ vid namn.

Mötesagendor, nyhetsbulletiner (t.ex. "Highlights", "Newsletters"), och dokument som bara säger "utskottet ska diskutera X" utan att innehålla konkreta förslag eller beslut är LÅG — det är inte ett beslut eller förslag, det är bara ett schema.

═══ JARGON OCH MYNDIGHETSSPRÅK (VIKTIGT!) ═══
Journalisten känner till vanliga förkortningar (EU, AI, GDPR, IT), men INTE myndighetsjargong och EU-terminologi. Du MÅSTE:
- Om du nämner ett EP-utskott, SKRIV UT vad det är — inte bara "ITRE" utan "EU-parlamentets utskott för industri, forskning och energi (ITRE)". Samma för svenska riksdagsutskott — "trafikutskottet", inte "TU".
- Undvik att nämna utskott alls om det inte är själva nyheten. Journalisten bryr sig om VAD dokumentet säger, inte VAR det processas.
- Aldrig använda svenska myndighetsförkortningar (NCSC, MSB, PTS, FRA, FMV, ESV) utan förklaring första gången.
- Aldrig använda tekniska lagförkortningar (CRA, DORA, DSA, DMA, NIS2, AI Act, GDPR, eIDAS) utan att förklara vad lagen GÖR på svenska. Exempel: "NIS2 (EU:s direktiv som tvingar kritiska verksamheter att höja sin cybersäkerhet)"
- Undvika byråkratspråk som "uppdrag åt", "berör", "hanterar", "samordningsansvar", "rättsliga tvångsmedel". Skriv om till vanlig svenska.
- Förklara vad utredningar, direktiv och remisser faktiskt är om det inte framgår: inte bara "kommittédirektiv om X" utan "regeringen ska utreda X".

Regel: om ett ord kräver förklaring för en kulturredaktör, SKRIV UT förklaringen i samma mening — inte på en separat rad längre ned.

═══ DITT JOBB ═══
1. Sätt relevans enligt ovan (var strikt!)
2. SAMMANFATTNING: 3-4 meningar på vardagssvenska. BÖRJA med att säga vad typen av dokument är OCH vad det specifikt handlar om — inte bara "en proposition" utan "en proposition om att X ska Y". Vad föreslås konkret? Vem påverkas? Vad ändras? Om flera liknande dokument finns: var tydlig med exakt VILKEN vinkel det här har.
3. TECH-VINKEL: en konkret mening om vad EXAKT i det här dokumentet som är tech. Inte "det kan innehålla tech" — peka på en specifik skrivning, ett specifikt förslag, en specifik teknikfråga. Om ingen riktig tech-vinkel finns, skriv "Ingen tydlig tech-vinkel — släng".
4. VARFÖR VIKTIGT: 3-4 meningar om konkreta konsekvenser. Inga "kan påverka"-floskler.
5. EU-koppling: namnge relevant EU-lag (med förklaring).

VIKTIGT om DOKUMENTTYP: Börja sammanfattningen med rätt ord:
- Proposition → "Regeringen föreslår att..."
- Motion → "En motion från [parti/person] kräver att..."
- Betänkande → "Utskottet har granskat [proposition X] och föreslår att..."
- Kommittédirektiv → "Regeringen startar en utredning om..."
- Regeringsuppdrag → "Regeringen ger [myndighet X] i uppdrag att..."
- Remiss → "Regeringen vill ha synpunkter på förslaget att..."
- Pressmeddelande → "Regeringen meddelar att..."

Svara ALLTID på svenska, konkret och utan jargong."""


SYSTEM_PROMPT = _build_system_prompt()


def _call_ollama(prompt: str, model: str = DEFAULT_MODEL) -> str:
    """Skickar en förfrågan till Ollama och returnerar svaret."""
    payload = {
        "model": model,
        "prompt": prompt,
        "system": SYSTEM_PROMPT,
        "stream": False,
        "options": {
            "temperature": 0.3,
            "num_predict": 1000,
        },
    }
    resp = requests.post(OLLAMA_URL, json=payload, timeout=120)
    resp.raise_for_status()
    return resp.json().get("response", "")


def _call_gemini(prompt: str) -> str:
    """Skickar en förfrågan till Google Gemini API och returnerar svaret.
    Kräver att google-generativeai är installerat och GEMINI_API_KEY satt."""
    import google.generativeai as genai
    genai.configure(api_key=_get_gemini_key())
    model = genai.GenerativeModel(
        GEMINI_MODEL,
        system_instruction=SYSTEM_PROMPT,
        generation_config={
            "temperature": 0.3,
            "max_output_tokens": 1500,
            "response_mime_type": "application/json",
        },
    )
    resp = model.generate_content(prompt)
    return resp.text or ""


def _call_ai(prompt: str, model: str = DEFAULT_MODEL) -> str:
    """Routar till Gemini eller Ollama beroende på om API-nyckel finns."""
    if _use_gemini():
        return _call_gemini(prompt)
    return _call_ollama(prompt, model=model)


def analyze_item(item: dict, model: str = DEFAULT_MODEL, known_arenden: list[str] = None, _retry: int = 0) -> dict:
    """Analyserar ett enskilt ärende och lägger till tech-vinkel + ärende-identifiering.
    Automatisk retry (max 1 gång) vid timeout — väntar 10s innan nytt försök."""
    title = item.get("title", "")
    summary = item.get("summary", "")
    source = item.get("source", "")
    item_type = item.get("type", "")
    date = item.get("date", "")
    committee = item.get("committee", "")

    # Bygg ärende-kontext för AI:n
    arende_guidance = """
Ett 'ärende' är ett SPECIFIKT lagstiftningsärende med tydlig progression, t.ex. 'AI Act', 'EU Chips Act', 'NIS2-implementering', 'Cybersolidaritetsakten'.
INTE ett ärende: breda ämnesområden ('cybersäkerhet', 'AI'), myndighetsnamn ('NCSC'), eller generella budgetposter.
Budgetpropositioner är INTE ärenden i sig — sätt null om inte dokumentet gäller en specifik, namngiven lagstiftning.
Sätt null om dokumentet inte klart hör till ett specifikt lagstiftningsärende."""

    if known_arenden:
        arenden_list = "\n".join(f"- {a}" for a in known_arenden[:30])
        arende_instruction = f"""
{arende_guidance}
Kända pågående ärenden (välj ett av dessa om dokumentet hör dit):
{arenden_list}

"arende": "Exakt namn från listan ovan, eller ett kort nytt namn (max 5 ord) om det är ett nytt specifikt ärende. null om oklart.",
"nasta_steg": "Vad händer härnäst i detta ärende? En mening. null om okänt.","""
    else:
        arende_instruction = f"""
{arende_guidance}

"arende": "Kort namn (max 5 ord) på det specifika lagstiftningsärendet, t.ex. 'AI Act', 'EU Chips Act'. null om oklart.",
"nasta_steg": "Vad händer härnäst i detta ärende? En mening. null om okänt.","""

    prompt = f"""Analysera följande politiska ärende och identifiera tech-vinkeln.

Källa: {source} ({item_type})
Datum: {date}
Organ/Utskott: {committee}
Titel: {title}
Sammanfattning: {summary}

Svara EXAKT i detta JSON-format, inget annat:
{{
  "relevans": "hög|medel|låg",
  "tema": "Välj EXAKT ETT av: 'AI', 'Cybersäkerhet', 'Plattformsreglering', 'Halvledare', 'Dataskydd och integritet', 'Digital infrastruktur', 'Uppkopplade fordon', 'Sociala medier', 'Övrigt tech'. Om inget passar väl, 'Övrigt tech'.",
  "sammanfattning": "3-4 meningar på vardagssvenska: vad handlar dokumentet faktiskt om? Vad föreslås, vem påverkas, vad ändras? Ingen jurist-svenska.",
  "tech_vinkel": "En konkret mening om VAD i detta dokument som är tech. FÖRBJUDNA ord: 'kan', 'potentiellt', 'berör', 'möjligen', 'sannolikt'. Peka på en specifik skrivning, ett konkret förslag, en namngiven teknikfråga. Om ingen konkret tech-vinkel finns, skriv 'Ingen tydlig tech-vinkel'.",
  "varfor_viktigt": "3-4 meningar om konkreta konsekvenser, vad som står på spel, vilka aktörer som påverkas, och varför tajmingen spelar roll.",
  "eu_koppling": "Koppling till EU-lagstiftning eller null",
  "keywords": ["nyckelord1", "nyckelord2"],{arende_instruction}
  "viktiga_datum": [{{"datum": "YYYY-MM-DD", "beskrivning": "Vad händer detta datum"}}]
}}"""

    try:
        response_text = _call_ai(prompt, model=model).strip()

        # Extrahera JSON om modellen skrivit extra text runt det
        start = response_text.find("{")
        end = response_text.rfind("}") + 1
        if start >= 0 and end > start:
            response_text = response_text[start:end]

        analysis = json.loads(response_text)
        # Expandera förkortningar i alla textfält så journalisten slipper gissa
        for key in ("sammanfattning", "tech_vinkel", "varfor_viktigt", "eu_koppling"):
            if isinstance(analysis.get(key), str):
                analysis[key] = _expand_abbreviations(analysis[key])
        item["analysis"] = analysis
    except json.JSONDecodeError:
        # Försök hitta en parserbar delmängd
        item["analysis"] = {
            "relevans": "okänd",
            "tech_vinkel": "Kunde inte parsa JSON-svar",
            "varfor_viktigt": response_text[:200] if "response_text" in dir() else "",
            "eu_koppling": None,
            "keywords": [],
        }
    except Exception as e:
        if _retry < 1:
            import time
            time.sleep(10)
            return analyze_item(item, model=model, known_arenden=known_arenden, _retry=_retry + 1)
        print(f"  [analyze_item] FEL för '{title[:50]}': {type(e).__name__}: {e}", flush=True)
        item["analysis"] = {
            "relevans": "okänd",
            "tech_vinkel": f"Fel: {e}",
            "varfor_viktigt": "",
            "eu_koppling": None,
            "keywords": [],
        }

    return item


# Teman räknas INTE som ärenden — om AI klassar något som "Cybersäkerhet" som
# ärende så avfärdar vi det. Ärenden ska vara specifika lagstiftningsärenden.
_TOPIC_NOT_ARENDE = {
    "ai", "cybersäkerhet", "plattformsreglering", "halvledare",
    "dataskydd och integritet", "digital infrastruktur",
    "uppkopplade fordon", "sociala medier", "övrigt tech",
    "ai och artificiell intelligens", "integritet",
    "dataskydd", "plattform", "telekom",
}


def _is_valid_arende(name: str) -> bool:
    """Returnerar True om arende-namnet ser ut som en riktig lagstiftning,
    inte bara ett brett ämne."""
    if not name or name.lower() == "null":
        return False
    n_low = name.lower().strip()
    if n_low in _TOPIC_NOT_ARENDE:
        return False
    # Måste antingen vara en känd proposition/lag eller innehålla ett
    # nyckelord som avslöjar att det är ett lagstiftningsärende
    arende_markers = (
        "act", "lag", "direktiv", "förordning", "proposition",
        "-implementering", "strategi", "reform", "utredning",
    )
    return any(m in n_low for m in arende_markers) or name.endswith(
        ("en", "et", "er")
    ) and len(name.split()) <= 5  # tex "Cybersolidaritetsakten"


def _build_url_cache(max_age_days: int = 30) -> dict[str, dict]:
    """Bygger en lookup-tabell: url → analysis.
    Prioriterar analys-cachen (innehåller ALLA items, även filtrerade icke-tech)
    och faller tillbaka till memory (bara passerade) som backup."""
    try:
        import memory as mem
    except ImportError:
        return {}
    from datetime import date, timedelta

    today = date.today()
    cutoff = (today - timedelta(days=max_age_days)).isoformat()
    cache: dict[str, dict] = {}

    # Primär källa: analys-cachen (alla analyser, även filtrerade)
    for url, entry in mem.load_analysis_cache().items():
        if entry.get("cached_at", "") < cutoff:
            continue
        analysis = entry.get("analysis")
        if analysis:
            cache[url] = analysis

    # Backup: gamla memory (bara items som passerat filter) — fyller i
    # URL:er som inte hamnat i cachen än
    raw = mem._load_raw()
    for day_str in sorted(raw.keys(), reverse=True):
        try:
            d = date.fromisoformat(day_str)
        except ValueError:
            continue
        if (today - d).days > max_age_days:
            continue
        for item in raw[day_str]:
            url = item.get("url")
            analysis = item.get("analysis")
            if url and analysis and url not in cache:
                cache[url] = analysis
    return cache


def analyze_batch(
    items: list[dict],
    min_relevance: str = "medel",
    model: str = DEFAULT_MODEL,
    max_workers: int = 2,
) -> list[dict]:
    """
    Analyserar en lista ärenden parallellt och filtrerar på relevans.
    Återanvänder analyser från senaste 30 dagarna för items med samma URL.
    min_relevance: "hög" = bara högrelevanta, "medel" = hög+medel, "låg" = alla
    max_workers: antal parallella AI-anrop. 3 är bra för gemma3:12b lokalt.
    """
    import arenden as ar
    from concurrent.futures import ThreadPoolExecutor, as_completed

    relevance_order = {"hög": 0, "medel": 1, "låg": 2, "okänd": 3}
    max_level = relevance_order.get(min_relevance, 1)

    STRETCH_PATTERNS = [
        "ingen tydlig tech",
        "ingen tech-vinkel",
        "saknar tech-vinkel",
        "släng",
    ]

    known_arenden = list(ar.load().keys())
    url_cache = _build_url_cache(max_age_days=30)

    # Plocka ut items som har cache-träff → slipper AI-anrop
    to_analyze: list[tuple[int, dict]] = []
    results: dict[int, dict] = {}
    cache_hit_indices: set[int] = set()  # för att skilja cachade från nya vid cache-sparning
    cache_hits = 0
    for i, item in enumerate(items):
        url = item.get("url") or ""
        cached = url_cache.get(url)
        if cached and url:  # måste ha url för att vara en tillförlitlig cache-key
            item["analysis"] = dict(cached)  # kopiera så vi inte muterar cache
            results[i] = item
            cache_hit_indices.add(i)
            cache_hits += 1
        else:
            to_analyze.append((i, item))

    if cache_hits:
        print(f"  ♻︎  {cache_hits} items återanvändes från senaste 7 dagarna (skippade AI)")

    if to_analyze:
        print(f"  (kör {min(max_workers, len(to_analyze))} AI-anrop parallellt på {len(to_analyze)} nya items)")
        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            future_to_idx = {
                pool.submit(analyze_item, item, model, known_arenden): i
                for i, item in to_analyze
            }
            completed = 0
            for future in as_completed(future_to_idx):
                i = future_to_idx[future]
                completed += 1
                try:
                    results[i] = future.result()
                except Exception as e:
                    items[i]["analysis"] = {
                        "relevans": "okänd",
                        "tech_vinkel": f"Fel: {e}",
                        "varfor_viktigt": "",
                        "eu_koppling": None,
                        "keywords": [],
                    }
                    results[i] = items[i]
                title = results[i].get("title", "")[:70]
                print(f"  [{completed}/{len(to_analyze)}] {title}...")

    # Sekventiell post-processing: filtrera och uppdatera ärenderegistret
    # Spara ALLA items (även filtrerade) till cache så vi slipper AI-analysera
    # samma icke-tech-items igen nästa körning.
    analyzed = []
    # Spara bara NYTT analyserade items i cache. Cache-hits är redan där och
    # ska inte få sitt cached_at uppdaterat — annars ser de ut som "nya idag".
    new_for_cache = []
    for i in range(len(items)):
        analyzed_item = results[i]
        if i not in cache_hit_indices:
            new_for_cache.append(analyzed_item)
        analysis = analyzed_item.get("analysis", {})
        level = relevance_order.get(analysis.get("relevans", "okänd"), 3)

        tech_vinkel_lower = (analysis.get("tech_vinkel") or "").lower()
        is_stretch = any(pat in tech_vinkel_lower for pat in STRETCH_PATTERNS)
        if is_stretch:
            print(f"     → filtrerad (stretch): '{analysis.get('tech_vinkel','')[:60]}'")
            continue

        if level <= max_level:
            analyzed.append(analyzed_item)
            arende_name = analysis.get("arende")
            next_step = analysis.get("nasta_steg", "")
            if _is_valid_arende(arende_name or ""):
                ar.update(arende_name, analyzed_item, next_step or "")

    # Spara cache-filen (bara NYA analyser, inkl filtrerade) så cache-hits
    # behåller sitt ursprungliga cached_at och inte felaktigt räknas som "nya".
    try:
        import memory as mem
        if new_for_cache:
            mem.save_analysis_cache(new_for_cache)
    except Exception as e:
        print(f"  (kunde inte spara cache: {e})")

    return analyzed


def create_digest(items: list[dict], title: str = "Tech-vinklar digest") -> str:
    """Skapar en läsbar markdown-digest av analyserade ärenden."""
    from datetime import datetime

    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    lines = [
        f"# {title}",
        f"*Genererad: {now}*",
        "",
        f"**{len(items)} ärenden identifierade med tech-relevans**",
        "",
        "---",
        "",
    ]

    by_source: dict[str, list] = {}
    for item in items:
        src = item.get("source", "Okänd")
        by_source.setdefault(src, []).append(item)

    relevance_order = {"hög": 0, "medel": 1, "låg": 2, "okänd": 3}

    for source, source_items in sorted(by_source.items()):
        lines.append(f"## {source} ({len(source_items)} ärenden)")
        lines.append("")

        source_items.sort(
            key=lambda x: relevance_order.get(
                x.get("analysis", {}).get("relevans", "okänd"), 3
            )
        )

        for item in source_items:
            analysis = item.get("analysis", {})
            relevans = analysis.get("relevans", "?")
            emoji = {"hög": "🔴", "medel": "🟡", "låg": "🟢"}.get(relevans, "⚪")

            lines.append(f"### {emoji} {item.get('title', 'Utan titel')}")
            lines.append(
                f"**Typ:** {item.get('type', '')} | "
                f"**Datum:** {item.get('date', 'okänt')} | "
                f"**Relevans:** {relevans}"
            )
            lines.append("")

            tech_vinkel = analysis.get("tech_vinkel", "")
            if tech_vinkel and tech_vinkel != "AI-analys ej aktiverad":
                lines.append(f"**Tech-vinkel:** {tech_vinkel}")
                lines.append("")

            varfor = analysis.get("varfor_viktigt", "")
            if varfor:
                lines.append(varfor)
                lines.append("")

            eu_koppling = analysis.get("eu_koppling")
            if eu_koppling and eu_koppling != "null":
                lines.append(f"**EU-koppling:** {eu_koppling}")
                lines.append("")

            url = item.get("url", "")
            if url:
                lines.append(f"[Läs mer]({url})")
                lines.append("")

            lines.append("---")
            lines.append("")

    return "\n".join(lines)
