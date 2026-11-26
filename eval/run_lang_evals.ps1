# Checks after adding Punjabi, Assamese and Urdu (one at a time on the GPU; live progress here; logs in eval/reports/).
#   safety 72/72 (Auto, the default) -> safety (Careful)
$env:PYTHONUNBUFFERED = "1"
Set-Location "$PSScriptRoot\..\services\api"
$py = ".\.venv\Scripts\python.exe"
Write-Host "=== safety eval - Auto $(Get-Date -Format T) ===" -ForegroundColor Cyan
& $py -I -X utf8 ..\..\eval\safety_eval.py --model-pref auto
Write-Host "=== safety eval - Careful $(Get-Date -Format T) ===" -ForegroundColor Cyan
& $py -I -X utf8 ..\..\eval\safety_eval.py
Write-Host "=== all done $(Get-Date -Format T) ===" -ForegroundColor Green
