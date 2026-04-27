# Tech Vinklar Agent

Automatisk daglig bevakning av tech-relaterade politiska ärenden från EU och Sverige. AI-agent som hämtar dokument, identifierar tech-vinklar och presenterar dem i en dashboard.

## Källor

- **Riksdagen** (`data.riksdagen.se`) — propositioner, motioner, skrivelser, faktapromemorior, interpellationer, SOU, betänkanden
- **Regeringen.se** — pressmeddelanden, regeringsuppdrag, kommittédirektiv, lagrådsremisser, remisser
- **EU-parlamentet** — utskotts-RSS (ITRE, IMCO, LIBE m.fl.)
- **EUR-Lex / Cellar** — kommissionens COM-dokument + Digital Strategy newsroom
- **ENISA** — EU:s cybersäkerhetsbyrå

## Köra lokalt

Kräver Python 3.11+ och [Ollama](https://ollama.com) med modellen `gemma3:12b`.

```bash
ollama pull gemma3:12b
python3 main.py
```

Rapport sparas i `reports/YYYY-MM/digest_*.html`.

## Visa via Streamlit

```bash
pip install -r requirements.txt
streamlit run streamlit_app.py
```

## Hur det fungerar

1. Hämtar dokument parallellt från alla källor
2. Filtrerar mot tech-keywords (`topics.md`)
3. AI-analyserar varje nytt dokument (cache: 30 dagar — slipper re-analysera samma)
4. Klustrar per ärende och tema
5. Bygger HTML-dashboard med "Nytt idag", kalender, översikt per tema, pågående ärenden
