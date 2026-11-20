#!/usr/bin/env bash
# Faster document reader candidates: the same three evals the default reader (qwen3-vl:4b) was measured on,
# run one model at a time (one 6 GB GPU). Results are compared with the stored baselines in
# eval/reports/fast_doc_model.json. Usage (from the repo root): bash eval/run_fast_doc_candidates.sh
set -u
cd "$(dirname "$0")/../services/api"
PY=.venv/Scripts/python
for M in qwen3-vl:2b-instruct granite3.2-vision:2b; do
  T=$(echo "$M" | tr ':.' '__')
  echo "=== $M rx $(date +%T)"
  $PY -I ../../eval/rx_eval.py ../../data/eval/rx_mirage100 --model "$M" > "../../eval/reports/run_fastdoc_${T}_rx.log" 2>&1
  echo "=== $M labs $(date +%T)"
  RX_VLM_MODEL="$M" $PY -I ../../eval/reports_eval.py labs > "../../eval/reports/run_fastdoc_${T}_labs.log" 2>&1
  echo "=== $M doctype $(date +%T)"
  $PY -I ../../eval/doc_type_eval.py --n 30 --other 10 --cxr 10 --model "$M" > "../../eval/reports/run_fastdoc_${T}_doctype.log" 2>&1
done
echo "=== done $(date +%T)"
