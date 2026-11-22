# After-evals, one at a time, ordered so each model loads once:
#   advice (llama3.2 Faster/Instant first, then MedGemma Careful) -> chat + safety on MedGemma -> chat + safety on llama3.2.
# Live per-case progress here; logs in eval/reports/.  Usage: & eval\run_after_evals.ps1
$env:PYTHONUNBUFFERED = "1"
Set-Location "$PSScriptRoot\..\services\api"
$py = ".\.venv\Scripts\python.exe"
$rep = "..\..\eval\reports"
function Show-Step($name) { Write-Host "=== $name $(Get-Date -Format T) ===" -ForegroundColor Cyan }

Show-Step "advice eval (all three modes)"
& $py -I ..\..\eval\advice_eval.py --tag after 2>&1 | Tee-Object -FilePath "$rep\run_advice_after.log"

Show-Step "chat eval - Careful (MedGemma)"
& $py ..\..\eval\chat_eval.py run --tag advice_balanced --answers 30 2>&1 | Tee-Object -FilePath "$rep\run_chat_advice_balanced.log"
Show-Step "safety eval - Careful (MedGemma)"
& $py -I ..\..\eval\safety_eval.py 2>&1 | Tee-Object -FilePath "$rep\run_safety_advice_balanced.log"

Show-Step "chat eval - Faster (llama3.2:3b)"
$env:LLM_MODEL = "llama3.2:3b"
& $py ..\..\eval\chat_eval.py run --tag advice_fast --answers 30 2>&1 | Tee-Object -FilePath "$rep\run_chat_advice_fast.log"
Remove-Item Env:\LLM_MODEL
Show-Step "safety eval - Faster setting"
& $py -I ..\..\eval\safety_eval.py --model-pref fast 2>&1 | Tee-Object -FilePath "$rep\run_safety_advice_fast.log"
Write-Host "=== all evals done $(Get-Date -Format T) ===" -ForegroundColor Green
