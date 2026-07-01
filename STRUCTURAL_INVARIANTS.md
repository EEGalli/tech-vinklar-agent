# Strukturella invarianter

När sajten har en regel som måste finnas på FLERA ställen — dokumentera den här.
Kör `python3 check_ai_prompt_sync.py` före push för att verifiera att invarianter håller.

## Varför den här filen finns

Sajten har både:
- **Retroaktiv logik** (filter/rendering) som körs på befintlig data
- **AI-prompter** som styr hur nya items analyseras

När Evelina säger en ny regel ("filtrera bort X", "granulärare teman", "prio max 20% hög")
måste den finnas i **båda** — annars kommer nya AI-körningar generera dålig data som sedan
måste efterfixas. Systemsystem.

## Aktuella invarianter (2026-07-01)

### 1. Tema-listor synkade

**Vad:** Listan över giltiga teman måste vara identisk i tre led.

**Var:**
- `output/html_report.py` — `TEMA_EMOJI` / `TEMA_ORDER` (rendering)
- `output/html_report.py` — `_TEMA_KEYWORD_RULES` (retroaktiv omkategorisering)
- `output/html_report.py` — `_LEGACY_TEMA_MAP` (mappa gamla → nya)
- `analyzer.py` — prompt-mallens `"tema": "Välj EXAKT ETT av: ..."` (nya items)

**Regel:** Om jag lägger till/tar bort ett tema — uppdatera ALLA fyra ställen.

### 2. Filter för workshops/events/interna admin

**Vad:** Items som är interna procedurer (workshops, hearings, regeringsuppdrag som
lämnas in) ska varken hamna hos AI eller efter analys.

**Var:**
- `streamlit_app.py` — `_INTERNAL_ADMIN_PATTERNS` + `_should_exclude` (efter-filter)
- `analyzer.py` — `EXCLUDE_PATTERNS` + `HIGHLIGHTS_EXCLUDE` (före-cache-filter)
- `analyzer.py` — `STRETCH_PATTERNS` (för AI-svar som säger "ingen tydlig tech-vinkel")
- `sources/eu_agencies.py` — `_EXCLUDE_PATTERNS` (per-källa)

**Regel:** Nya filter-mönster hör hemma i BÅDA `analyzer.py` (så AI-tokens sparas) OCH
`streamlit_app.py` (så cache-items filtreras vid rendering).

### 3. Nedgradering av kollektionsdokument (Survey/Consultation)

**Vad:** Titlar som börjar med "Survey on", "Consultation on", "Draft guidelines" osv
ska aldrig vara hög-prio.

**Var:**
- `analyzer.py` — `_DOWNGRADE_PREFIXES` (post-process av AI-svar)
- `analyzer.py` — prompt-mall (instruktion om max 20% hög-prio)

**Regel:** Om jag lägger till en ny "nedgradera"-mönster — uppdatera BÅDA så AI:n vet det
OCH vi efterfixar det AI:n missar.

### 4. Prioritet-overrides (.agent_overrides.json)

**Vad:** Manuella prio-ändringar från användaren måste respekteras.

**Var:**
- `analyzer.py` — `_load_relevans_overrides()` läser vid varje analys
- `learning.py` — genererar `learning_hint` av mönster till nya AI-anrop
- `output/html_report.py` — `applyOverridesOnLoad()` JS applicerar vid sidladdning
- `streamlit_app.py` — filen finns i repo:t och committas efter ändringar

**Regel:** Ny relevans-värde (t.ex. "utesluten") måste hanteras i ALLA fyra ställen.

### 5. Datum-tolkning (RSS + ISO)

**Vad:** RSS-datum ("Mon, 01 Jan 2026 12:00:00 +0000") och ISO-datum ("2026-06-30")
måste båda tolkas korrekt överallt.

**Var:**
- `output/html_report.py` — `_parse_date()` / `_format_item_date()` / `_date_sort_key()`
- `arenden.py` — `_to_iso_date()` (normalisering vid save)
- `memory.py` — sortering och filtrering

**Regel:** Om nytt datum-format dyker upp — uppdatera `_parse_date` (central) så alla
downstream-funktioner automatiskt fungerar.

### 6. Källa → Streamlit-tabb-mappning

**Vad:** Varje ny källa (t.ex. IMY, EDRi) måste mappas till rätt Streamlit-flik.

**Var:**
- `sources/` — filen som hämtar (definierar `source`-fältet)
- `streamlit_app.py` — `SOURCE_TO_TAB` (rutning till tabb)

**Regel:** Ny källa = uppdatera BÅDA. Annars syns items inte i sin tabb.

## Hur man använder check-scriptet

```bash
python3 check_ai_prompt_sync.py
```

Exit 0 = allt synkat. Exit 1 = osync, se felmeddelanden.

Om check-scriptet ger falsk-positiv (ordval i prompt skiljer men logiken är samma),
uppdatera regex-en i `check_ai_prompt_sync.py`.

## Hur man lägger till en ny invariant

1. Dokumentera invarianten här (vad + var + regel)
2. Lägg till en `check_xxx()`-funktion i `check_ai_prompt_sync.py`
3. Anropa den från `main()`
