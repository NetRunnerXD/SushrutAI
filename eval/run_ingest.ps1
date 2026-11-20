# Ingest the cached new sources (embeds with bge-m3 on the GPU: run alone), then pick the "consulted" threshold.
# Live progress here; logs in eval/reports/.  Usage: & eval\run_ingest.ps1
$env:PYTHONUNBUFFERED = "1"
Set-Location "$PSScriptRoot\..\services\api"
foreach ($src in "medlineplus-all", "cdc", "who", "nhs") {
    Write-Host "=== ingest $src $(Get-Date -Format T) ===" -ForegroundColor Cyan
    & .\.venv\Scripts\python.exe -m app.pipelines.kb $src 2>&1 | Tee-Object -FilePath "..\..\eval\reports\run_ingest_$src.log"
}
Write-Host "=== consulted-source threshold $(Get-Date -Format T) ===" -ForegroundColor Cyan
& .\.venv\Scripts\python.exe -I ..\..\eval\consult_threshold.py 2>&1 | Tee-Object -FilePath "..\..\eval\reports\run_consult_threshold.log"
Write-Host "=== ingest done $(Get-Date -Format T) ===" -ForegroundColor Green
