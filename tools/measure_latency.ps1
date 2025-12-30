param(
    [string]$Dataset = 'data/test_emails.json',
    [int]$Warmup = 1,
    [int]$Iterations = 3
)

$root = Split-Path -Parent $MyInvocation.MyCommand.Path
Push-Location $root
try {
    $env:MODEL_BACKEND = 'ollama'
    if (-not $env:OLLAMA_MODEL) { $env:OLLAMA_MODEL = 'llama3.1:8b' }
    if (-not $env:OLLAMA_HOST) { $env:OLLAMA_HOST = 'http://127.0.0.1:11434' }

    $cmd = "..\.venv\\Scripts\\python.exe"
    if (-not (Test-Path $cmd)) { $cmd = 'python' }

    $args = @(
        '..\\tools\\benchmark_pipeline.py',
        '--dataset', $Dataset,
        '--output', 'data\\benchmark_report.xlsx'
    )

    if ($Warmup -gt 0) {
        Write-Host "Running warmup ($Warmup iteration)" -ForegroundColor Yellow
        for ($i = 1; $i -le $Warmup; $i++) {
            & $cmd @args | Out-Null
        }
    }

    Write-Host "Measuring latency ($Iterations runs)" -ForegroundColor Cyan
    $results = @()
    for ($i = 1; $i -le $Iterations; $i++) {
        $sw = [System.Diagnostics.Stopwatch]::StartNew()
        $output = & $cmd @args
        $sw.Stop()
        $results += [pscustomobject]@{
            Iteration = $i
            WallClockSeconds = [math]::Round($sw.Elapsed.TotalSeconds, 3)
        }
    }

    $results | Format-Table -AutoSize
    $avg = ($results | Measure-Object -Property WallClockSeconds -Average).Average
    Write-Host "Average wall-clock: $([math]::Round($avg,3)) seconds" -ForegroundColor Green
    Write-Host "Detailed per-email metrics saved to data\\benchmark_report.xlsx" -ForegroundColor Green
}
finally {
    Pop-Location
}
