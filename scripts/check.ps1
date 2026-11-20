<#
.SYNOPSIS
  Runs every quality check for Macro Studio and exits non-zero if any fails.

.DESCRIPTION
  ruff (lint), pyright (types), bandit (security) and pytest, in that order, using the
  project's .venv. Each tool runs even if an earlier one failed, so one run shows
  everything. Create the environment once with:

    uv venv --python 3.12 .venv
    uv pip install --python .venv\Scripts\python.exe -r requirements-dev.txt

  -Format also runs `ruff format --check`. It is off by default because the existing code
  was not written with ruff format; turn it on once the code has been formatted.
  -SkipTests skips pytest (the slowest step) for a quick static check.
#>
param(
    [switch]$Format,
    [switch]$SkipTests
)

$ErrorActionPreference = "Continue"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root
$py = Join-Path $root ".venv\Scripts\python.exe"
if (-not (Test-Path $py)) {
    Write-Host "No .venv found. Run:  uv venv --python 3.12 .venv ; uv pip install --python .venv\Scripts\python.exe -r requirements-dev.txt"
    exit 2
}

# Some source lines contain emoji; without this bandit crashes on the cp1252 console.
$env:PYTHONIOENCODING = "utf-8"

$results = [ordered]@{}

function Invoke-Check {
    param([string]$Name, [string[]]$Arguments)
    Write-Host ""
    Write-Host "=== $Name ==="
    & $py @Arguments
    $script:results[$Name] = $LASTEXITCODE
}

Invoke-Check "ruff" @("-m", "ruff", "check", ".")
if ($Format) { Invoke-Check "ruff format" @("-m", "ruff", "format", "--check", ".") }
Invoke-Check "pyright" @("-m", "pyright")
Invoke-Check "bandit" @("-m", "bandit", "-q", "-r", "macro_studio", "-c", "pyproject.toml")
if (-not $SkipTests) { Invoke-Check "pytest" @("-m", "pytest", "-q") }

Write-Host ""
Write-Host "=== Summary ==="
$failed = 0
foreach ($name in $results.Keys) {
    $code = $results[$name]
    if ($code -eq 0) { $label = "PASS" } else { $label = "FAIL (exit $code)"; $failed++ }
    Write-Host ("{0,-12} {1}" -f $name, $label)
}
if ($failed -gt 0) { exit 1 }
exit 0
