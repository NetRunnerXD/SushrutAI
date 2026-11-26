# Checks after the voice-first upgrade (one at a time on the GPU; live progress here; reports in eval/reports/).
#   voice commands -> value extraction -> differential questions (GPU) -> safety 72/72 Auto -> safety Careful
$env:PYTHONUNBUFFERED = "1"
Set-Location "$PSScriptRoot\..\services\api"
$py = ".\.venv\Scripts\python.exe"
Write-Host "=== voice commands $(Get-Date -Format T) ===" -ForegroundColor Cyan
& $py -I -X utf8 ..\..\eval\voice_intent_eval.py
Write-Host "=== value extraction $(Get-Date -Format T) ===" -ForegroundColor Cyan
& $py -I -X utf8 ..\..\eval\extract_eval.py
Write-Host "=== differential questions (GPU) $(Get-Date -Format T) ===" -ForegroundColor Cyan
& $py -I -X utf8 ..\..\eval\differential_eval.py
Write-Host "=== safety eval - Auto $(Get-Date -Format T) ===" -ForegroundColor Cyan
& $py -I -X utf8 ..\..\eval\safety_eval.py --model-pref auto
Write-Host "=== safety eval - Careful $(Get-Date -Format T) ===" -ForegroundColor Cyan
& $py -I -X utf8 ..\..\eval\safety_eval.py
Write-Host "=== all done $(Get-Date -Format T) ===" -ForegroundColor Green
