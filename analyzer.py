"""
Analyserar insamlade politiska ärenden med AI för att identifiera tech-vinklar.

Stödjer tre backends (prioritetsordning):
1. Groq API (cloud, snabb, 14400/dag gratis) — om GROQ_API_KEY finns
2. Google Gemini API (cloud) — om GEMINI_API_KEY finns men Groq saknas
3. Ollama (lokalt) — fallback när inga cloud-nycklar finns ELLER när molnet 429:ar

Nycklar kan komma från: miljövariabel, .env-fil, eller Streamlit secrets.
"""
import json
import os
import re
import requests

OLLAMA_URL = "http://localhost:11434/api/generate"
DEFAULT_MODEL = "gemma3:12b"  # för Ollama

GEMINI_MODEL = "gemini-2.0-flash"
GROQ_MODEL = "llama-3.3-70b-versatile"  # snabb, OpenAI-kompatibel, 14400/dag gratis


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


def _get_secret(name: str) -> str:
    """Hämtar en API-nyckel från miljö eller Streamlit secrets."""
    value = os.environ.get(name, "")
    if value and not value.startswith("KLISTRA_IN"):
        return value
    try:
        import streamlit as st
        return st.secrets.get(name, "")
    except Exception:
        return ""


def _get_gemini_key() -> str:
    return _get_secret("GEMINI_API_KEY")


def _get_groq_key() -> str:
    return _get_secret("GROQ_API_KEY")


def _use_gemini() -> bool:
    return bool(_get_gemini_key())


def _use_groq() -> bool:
    return bool(_get_groq_key())


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
Först: ÄR det tech-relevant? Krav: konkret koppling till en namngiven lag, ett konkret beslut eller en specifik teknik. Om du bara kan säga "kan påverka tech" / "indirekt koppling" → sätt relevans="låg".

═══ HÖG / MEDEL / LÅG — KRITERIER ═══
Du är RESTRIKTIV med "hög". Max ~20% av items ska vara hög per körning.

HÖG (sparas för riktiga nyheter):
- Svensk proposition, lagrådsremiss eller SOU med direkt tech-innehåll som överlämnas/publiceras
- Regeringsbeslut/uppdrag som styr en specifik tech-fråga (t.ex. "Mediemyndigheten kartlägger algoritmer")
- EU-kommissionens officiella förslag, slutgiltiga beslut eller tillsynsbeslut (t.ex. "DSA-utredning mot Pornhub", "AI Act-genomförande beslutat")
- Kommittédirektiv om en specifik tech-fråga
- Nyheter om sanktioner, böter, viktiga domar

MEDEL (förberedande/informativt):
- EU-konsultationer ("Have your say", "Consultation on draft guidelines")
- Utkast till riktlinjer, surveys, rapporter
- Pressmeddelanden om kommande utredningar (utan beslut än)
- BEREC/EDPB/ENISA-opinions och guidelines

LÅG (skippa nästan alltid):
- Möten, hearings, presentations, voting time
- Newsletters, översikter, FAQ
- Generella beskrivningar utan namngivet beslut
- "Highlights - Exchange of views" / "Highlights - Presentation of"

Om titeln BÖRJAR med något av nedan → MAX medel (aldrig hög):
"Survey on", "Consultation on", "Draft guidelines", "Highlights -", "Newsletters", "Latest news"

Budgetpropositioner och vårpropositioner är LÅG om de inte nämner ett specifikt tech-initiativ vid namn.

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


def _call_groq(prompt: str) -> str:
    """Skickar förfrågan till Groq API (OpenAI-kompatibel). Snabb + generös gratis tier."""
    resp = requests.post(
        "https://api.groq.com/openai/v1/chat/completions",
        headers={
            "Authorization": f"Bearer {_get_groq_key()}",
            "Content-Type": "application/json",
        },
        json={
            "model": GROQ_MODEL,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": prompt},
            ],
            "temperature": 0.3,
            "max_tokens": 1500,
            "response_format": {"type": "json_object"},
        },
        timeout=60,
    )
    resp.raise_for_status()
    data = resp.json()
    return data["choices"][0]["message"]["content"]


def _is_ollama_available() -> bool:
    """Pingar Ollama på localhost — snabb check (2s timeout)."""
    try:
        r = requests.get("http://localhost:11434/api/tags", timeout=2)
        return r.status_code == 200
    except Exception:
        return False


_QUOTA_KEYWORDS = ("ResourceExhausted", "429", "quota", "rate limit", "RATE_LIMIT", "rate_limit")


def _call_ai(prompt: str, model: str = DEFAULT_MODEL) -> str:
    """Routar AI-anrop med automatiska fallbacks.
    Prioritet: Groq → Gemini → Ollama. Faller över vid ALLA fel (kvot, nätverk,
    timeout, 5xx) — inte bara kvot — så ett tillfälligt strul inte stjälper körningen."""
    # 1. Groq (primärt)
    if _use_groq():
        try:
            return _call_groq(prompt)
        except Exception as e:
            err = str(e)
            is_quota = any(kw in err for kw in _QUOTA_KEYWORDS)
            reason = "Groq-kvot slut" if is_quota else f"Groq-fel ({type(e).__name__}: {err[:80]})"
            # Försök Gemini om tillgänglig
            if _use_gemini():
                print(f"  ⚠ {reason} — fallback till Gemini", flush=True)
                try:
                    return _call_gemini(prompt)
                except Exception as ge:
                    g_err = str(ge)
                    g_quota = any(kw in g_err for kw in _QUOTA_KEYWORDS)
                    g_reason = "Gemini-kvot slut" if g_quota else f"Gemini-fel ({type(ge).__name__}: {g_err[:80]})"
                    if _is_ollama_available():
                        print(f"  ⚠ {g_reason} — fallback till Ollama", flush=True)
                        return _call_ollama(prompt, model=model)
                    raise
            # Ingen Gemini → prova Ollama direkt
            if _is_ollama_available():
                print(f"  ⚠ {reason} — fallback till Ollama", flush=True)
                return _call_ollama(prompt, model=model)
            raise

    # 2. Gemini (om Groq saknas)
    if _use_gemini():
        try:
            return _call_gemini(prompt)
        except Exception as e:
            err = str(e)
            is_quota = any(kw in err for kw in _QUOTA_KEYWORDS)
            reason = "Gemini-kvot slut" if is_quota else f"Gemini-fel ({type(e).__name__}: {err[:80]})"
            if _is_ollama_available():
                print(f"  ⚠ {reason} — fallback till Ollama", flush=True)
                return _call_ollama(prompt, model=model)
            raise

    # 3. Ollama (lokalt)
    return _call_ollama(prompt, model=model)


def analyze_item(item: dict, model: str = DEFAULT_MODEL, known_arenden: list[str] = None,
                 _retry: int = 0, learning_hint: str = "") -> dict:
    """Analyserar ett enskilt ärende och lägger till tech-vinkel + ärende-identifiering.
    Automatisk retry (max 1 gång) vid timeout — väntar 10s innan nytt försök.
    learning_hint: text med användarens preferenser från tidigare prioritet-val."""
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
{learning_hint}
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

    response_text = ""
    try:
        response_text = _call_ai(prompt, model=model).strip()

        # Vissa modeller wrappar JSON i markdown-kodblock — strippa det.
        if response_text.startswith("```"):
            # Ta bort första raden (```json) och sista (```)
            response_text = "\n".join(response_text.split("\n")[1:-1]).strip()

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
        # Nedgradera relevans för förberedande/informativa titlar — aldrig hög
        _DOWNGRADE_PREFIXES = (
            "Survey on", "Consultation on", "Draft guidelines",
            "Draft Commission guidelines", "Targeted consultation",
            "Highlights -", "Newsletters", "Latest news",
            "Have your say", "Call for", "Public consultation",
        )
        if (analysis.get("relevans") == "hög"
                and any(title.startswith(p) for p in _DOWNGRADE_PREFIXES)):
            analysis["relevans"] = "medel"
        item["analysis"] = analysis
    except json.JSONDecodeError as e:
        # Trasig JSON — retry en gång, sen logga med URL så det går att granska
        if _retry < 1:
            print(f"  ↻ JSON trasig för '{title[:50]}' — retry", flush=True)
            import time
            time.sleep(3)
            return analyze_item(item, model=model, known_arenden=known_arenden,
                                _retry=_retry + 1, learning_hint=learning_hint)
        print(
            f"  ⚠ JSON-fel kvar efter retry: '{title[:50]}' "
            f"({item.get('url','')}) — råsvar (200 första): {response_text[:200]}",
            flush=True,
        )
        item["analysis"] = {
            "relevans": "okänd",
            "tech_vinkel": "Kunde inte tolka AI:ns svar (JSON-fel)",
            "varfor_viktigt": response_text[:200],
            "eu_koppling": None,
            "keywords": [],
        }
    except Exception as e:
        # Nätverk/timeout — retry upp till 2 ggr med ökande väntetid
        if _retry < 2:
            import time
            wait = 10 * (2 ** _retry)  # 10s, 20s
            print(f"  ↻ AI-fel för '{title[:50]}' ({type(e).__name__}) — väntar {wait}s, retry {_retry+1}/2", flush=True)
            time.sleep(wait)
            return analyze_item(item, model=model, known_arenden=known_arenden,
                                _retry=_retry + 1, learning_hint=learning_hint)
        print(f"  ⚠ AI-fel kvar efter retries för '{title[:50]}' ({item.get('url','')}): "
              f"{type(e).__name__}: {e}", flush=True)
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
    # Brett budget-/regeringsdokument — vi vill ta tech-vinklar UR dem,
    # inte ha själva propositionen som ett ärende-namn
    "vårpropositionen", "budgetpropositionen", "vårproposition",
    "budgetproposition", "höstbudgeten", "vårändringsbudgeten",
    "höständringsbudgeten", "statsbudget", "regeringsförklaringen",
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


def _load_relevans_overrides() -> dict[str, str]:
    """Läser manuella prioritet-overrides från .agent_overrides.json.
    Format: {url: "hög"|"medel"|"låg"}. Användaren sätter dessa via ✏️-knappen
    i HTML-rapporten och committar JSON-filen till repo:t."""
    import json, os
    path = os.path.join(os.path.dirname(__file__), ".agent_overrides.json")
    if not os.path.exists(path):
        return {}
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        # Normalisera värden — ta emot både {url: "hög"} och {url: {"relevans": "hög"}}
        result = {}
        for url, val in data.items():
            if isinstance(val, str):
                result[url] = val
            elif isinstance(val, dict) and "relevans" in val:
                result[url] = val["relevans"]
        return result
    except Exception as e:
        print(f"  ⚠ KORRUPT overrides-fil ({path}): {e}", flush=True)
        return {}


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

    # Central event/jobb/marknadsförings-filter — användaren vill ha policy,
    # inte happenings. Slänga items där titel/summary matchar exklusivord.
    EXCLUDE_PATTERNS = (
        # Events
        "workshop", "conference", "summit", "webinar", "training session",
        "save the date", "hackathon", "info session", "info day",
        "registration is open", "exhibition", "press kit", "media kit",
        # Jobb
        "vacancy", "recruitment", "traineeship", "internship",
        "we are hiring", "join our team", "applications open",
        # Marknadsföring/admin
        "brochure", "leaflet", "rollup", "newsletter ",
        "composition of the management board", "composition of the board",
        "annual report on the use",
    )

    # Mer aggressiva mönster för EP "Highlights -" som ofta är events/meetings
    HIGHLIGHTS_EXCLUDE = (
        "exchange of views", "presentation of", "hearing on", "study presentation",
        "meeting of", "icm on", "structured dialogue", "voting time",
        "consideration of draft", "joint hearing", "public hearing",
    )

    def _is_excluded(item: dict) -> bool:
        title = item.get("title", "")
        text = f"{title} {item.get('summary','')}".lower()
        if any(p in text for p in EXCLUDE_PATTERNS):
            return True
        # EP "Highlights - X" där X är ett mötesreferens
        if title.startswith("Highlights"):
            tl = title.lower()
            if any(p in tl for p in HIGHLIGHTS_EXCLUDE):
                return True
        return False

    # Steg 0: släng events/jobb/marknadsföring INNAN cache + AI
    before = len(items)
    items = [i for i in items if not _is_excluded(i)]
    excluded = before - len(items)
    if excluded:
        print(f"  ⊘  {excluded} items filtrerade (events/jobb/marknadsföring)")

    known_arenden = list(ar.load().keys())
    url_cache = _build_url_cache(max_age_days=30)

    # Lärdomar från användarens tidigare prioritet-ändringar
    try:
        import learning
        patterns = learning.extract_patterns()
        learning_hint = learning.build_prompt_hint(patterns)
        if learning_hint:
            n_arende = len(patterns.get("arende", {}))
            n_kw = len(patterns.get("keyword", {}))
            n_src = len(patterns.get("source", {}))
            print(f"  🎓 Lärdomar från manuella val: {n_arende} ärenden, {n_kw} nyckelord, {n_src} källor")
    except Exception as e:
        print(f"  (kunde inte ladda lärdomar: {e})")
        patterns = {}
        learning_hint = ""

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
                pool.submit(analyze_item, item, model, known_arenden, 0, learning_hint): i
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

    # Applicera manuella prioritet-overrides från .agent_overrides.json
    # (skapas när användaren ändrar prioritet i HTML-rapporten via ✏️-knappen)
    overrides = _load_relevans_overrides()
    if overrides:
        override_count = 0
        for i in range(len(items)):
            url = results[i].get("url", "")
            if url and url in overrides:
                new_rel = overrides[url]
                if new_rel in ("hög", "medel", "låg"):
                    results[i].setdefault("analysis", {})["relevans"] = new_rel
                    results[i]["analysis"]["_manually_set"] = True
                    override_count += 1
        if override_count:
            print(f"  ✏️ Applicerade {override_count} manuella prioritet-overrides")

    # Säkerhetsnät: applicera mönster från tidigare manuella val på items
    # som inte har explicit override. Kräver 3+ enstämmiga tidigare val
    # innan vi overridar AI:n.
    if patterns:
        try:
            import learning
            learned_count = 0
            for i in range(len(items)):
                url = results[i].get("url", "")
                if url and url in overrides:
                    continue  # explicit override redan satt
                analysis = results[i].get("analysis", {}) or {}
                if analysis.get("_manually_set"):
                    continue
                arende = analysis.get("arende") or ""
                keywords = analysis.get("keywords") or []
                source = results[i].get("source", "")
                committee = results[i].get("committee", "")
                suggested, motivering = learning.suggest_relevans(
                    arende, keywords, source, committee, patterns=patterns,
                )
                if suggested and suggested != analysis.get("relevans"):
                    old = analysis.get("relevans", "okänd")
                    analysis["relevans"] = suggested
                    analysis["_learned_from"] = motivering
                    learned_count += 1
                    title_short = results[i].get("title", "")[:60]
                    print(f"  🎓 '{title_short}' {old} → {suggested} ({motivering})")
            if learned_count:
                print(f"  🎓 Säkerhetsnät: justerade {learned_count} items baserat på dina tidigare val")
        except Exception as e:
            print(f"  (säkerhetsnät fel: {e})")

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
