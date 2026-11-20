#!/usr/bin/env bash
# Second step of the faster document reader comparison: granite3.2-vision:2b on prescriptions and lab reports.
# (The document-type check in chat stays on the default reader, so it is not re-evaluated.)
set -u
cd "$(dirname "$0")/../services/api"
PY=.venv/Scripts/python
M=granite3.2-vision:2b
T=granite3_2-vision_2b
echo "=== $M rx $(date +%T)"
$PY -I ../../eval/rx_eval.py ../../data/eval/rx_mirage100 --model "$M" > "../../eval/reports/run_fastdoc_${T}_rx.log" 2>&1
echo "=== $M labs $(date +%T)"
RX_VLM_MODEL="$M" $PY -I ../../eval/reports_eval.py labs > "../../eval/reports/run_fastdoc_${T}_labs.log" 2>&1
echo "=== done $(date +%T)"
