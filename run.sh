#!/bin/bash
# Tech Vinklar Agent — körs automatiskt varje morgon kl 07:00
# Kör manuellt med: bash run.sh

PYTHON=/Library/Developer/CommandLineTools/Library/Frameworks/Python3.framework/Versions/3.9/bin/python3
PROJECT="/Users/evelinagalli/Desktop/Agent Techvinklar EU : Riksdan"
LOG="$PROJECT/agent.log"

echo "=== $(date '+%Y-%m-%d %H:%M') ===" >> "$LOG"
cd "$PROJECT" && $PYTHON main.py --min-relevance medel >> "$LOG" 2>&1
echo "Klart." >> "$LOG"
