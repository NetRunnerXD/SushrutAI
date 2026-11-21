#!/usr/bin/env bash
# Faster/Instant chat model candidates, measured the same way as the stored MedGemma ("speed_balanced") and
# qwen2.5:3b ("speed_fast") runs: the candidate answers every chat-eval case and the full safety set.
# One model at a time (one 6 GB GPU). Usage (from the repo root): bash eval/run_chat_candidates.sh
set -u
cd "$(dirname "$0")/../services/api"
PY=.venv/Scripts/python
for M in qwen3:1.7b llama3.2:3b; do
  T=$(echo "$M" | tr ':.' '__')
  echo "=== $M chat $(date +%T)"
  LLM_MODEL="$M" $PY ../../eval/chat_eval.py run --tag "cand_${T}" --answers 30 > "../../eval/reports/run_chat_${T}.log" 2>&1
  echo "=== $M safety $(date +%T)"
  LLM_MODEL="$M" $PY -I ../../eval/safety_eval.py > "../../eval/reports/run_safety_${T}.log" 2>&1
done
echo "=== done $(date +%T)"
