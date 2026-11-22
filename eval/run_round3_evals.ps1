# Round-3 checks, one at a time on the GPU, live progress here; logs in eval/reports/.
#   follow-up topic carry-over (two-turn chats) -> safety 72/72 (Careful) -> safety (Auto)
$env:PYTHONUNBUFFERED = "1"
Set-Location "$PSScriptRoot\..\services\api"
$py = ".\.venv\Scripts\python.exe"
Write-Host "=== follow-up eval $(Get-Date -Format T) ===" -ForegroundColor Cyan
& $py -I -X utf8 ..\..\eval\followup_eval.py
Write-Host "=== safety eval - Careful $(Get-Date -Format T) ===" -ForegroundColor Cyan
& $py -I -X utf8 ..\..\eval\safety_eval.py
Write-Host "=== safety eval - Auto $(Get-Date -Format T) ===" -ForegroundColor Cyan
& $py -I -X utf8 ..\..\eval\safety_eval.py --model-pref auto
Write-Host "=== all done $(Get-Date -Format T) ===" -ForegroundColor Green
