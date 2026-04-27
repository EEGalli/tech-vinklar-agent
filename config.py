"""
Konfiguration för Tech Vinklar Agent.

Redigera topics.md för att styra vilka ämnen som bevakas.
Den här filen hanterar tekniska inställningar.
"""
import os
import re

# ── Tekniska inställningar ────────────────────────────────────────
OLLAMA_MODEL = "gemma3:12b"
LOOKAHEAD_DAYS = 14

# Riksdagsutskott med tech-relevans
TECH_RELEVANT_COMMITTEES = [
    "NU",   # Näringsutskottet
    "TU",   # Trafikutskottet (digital infrastruktur)
    "FiU",  # Finansutskottet (fintech, betalningar)
    "KU",   # Konstitutionsutskottet (integritet)
    "FöU",  # Försvarsutskottet (cybersäkerhet)
    "SoU",  # Socialutskottet (e-hälsa)
    "UbU",  # Utbildningsutskottet (AI i skola)
]

# EP-utskott med tech-relevans
TECH_EP_COMMITTEES = {"ITRE", "IMCO", "LIBE", "TRAN", "ECON", "JURI", "AIDA"}

# ── Dynamisk ämneslista från topics.md ───────────────────────────
def _load_topics() -> tuple[list[str], list[str], list[str]]:
    """
    Läser topics.md och returnerar (high_priority, lower_priority, exclude).
    Om filen saknas används inbyggda standardvärden.
    """
    topics_path = os.path.join(os.path.dirname(__file__), "topics.md")

    if not os.path.exists(topics_path):
        return _default_keywords(), [], []

    try:
        with open(topics_path, encoding="utf-8") as f:
            content = f.read()

        high, low, excl = [], [], []
        current_section = None

        for line in content.splitlines():
            line = line.strip()
            if "## Hög prioritet" in line:
                current_section = "high"
            elif "## Lägre prioritet" in line:
                current_section = "low"
            elif "## Exkludera" in line:
                current_section = "excl"
            elif line.startswith("- ") and current_section:
                # Extrahera nyckelordet (text före eventuell parentes)
                raw = line[2:].strip()
                keyword = re.split(r"\(", raw)[0].strip().lower()
                if current_section == "high":
                    high.append(keyword)
                    # Extrahera också termer inom parentes
                    parens = re.findall(r"\(([^)]+)\)", raw)
                    for p in parens:
                        for sub in p.split(","):
                            high.append(sub.strip().lower())
                elif current_section == "low":
                    low.append(keyword)
                elif current_section == "excl":
                    excl.append(keyword)

        return high, low, excl
    except Exception:
        return _default_keywords(), [], []


def _default_keywords() -> list[str]:
    return [
        "digital", "teknik", "teknologi", "ai", "artificiell intelligens",
        "data", "cybersäkerhet", "integritet", "personuppgifter", "gdpr",
        "internet", "bredband", "telekom", "5g", "halvledare",
        "mjukvara", "algoritm", "plattform", "e-handel",
        "kryptovaluta", "blockchain", "sociala medier",
        "desinformation", "deepfake", "automatisering",
        "artificial intelligence", "machine learning", "cybersecurity",
        "data protection", "privacy", "semiconductor", "cloud",
        "disinformation", "automation", "robotics", "cryptocurrency",
        "digital services", "technology", "quantum", "satellite",
        "DSA", "DMA", "AI Act", "NIS2", "DORA",
    ]


# Ladda in vid import
HIGH_PRIORITY_TOPICS, LOWER_PRIORITY_TOPICS, EXCLUDE_TOPICS = _load_topics()

# TECH_KEYWORDS = alla hög + lägre (används för pre-filtrering)
TECH_KEYWORDS = list(set(HIGH_PRIORITY_TOPICS + LOWER_PRIORITY_TOPICS))

# Backup om topics.md är tom
if not TECH_KEYWORDS:
    TECH_KEYWORDS = _default_keywords()
