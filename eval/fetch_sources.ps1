# Download new knowledge-base sources into data/sources/web_cache (network only; no database or GPU use).
Set-Location "$PSScriptRoot\..\services\api"
foreach ($src in "medlineplus-all", "cdc", "who", "nhs") {
    Write-Host "=== $src ===" -ForegroundColor Cyan
    & .\.venv\Scripts\python.exe -m app.pipelines.kb --fetch-only $src
}
Write-Host "=== fetch done ===" -ForegroundColor Green
