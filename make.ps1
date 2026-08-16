# Windows shim for the Makefile.
#
# `make` is not present on a stock Windows install. Rather than require a
# toolchain install just to read a task list, this maps the same target names
# onto the same commands.
#
#   ./make.ps1 setup
#   ./make.ps1 up
#   ./make.ps1 seed

param(
    [Parameter(Position = 0)]
    [string]$Target = 'help'
)

$ErrorActionPreference = 'Stop'
Set-Location $PSScriptRoot

# Everything Python runs out of the project-local venv. Falls back to the
# interpreter on PATH only so that `./make.ps1 setup` can bootstrap.
$Py = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
if (-not (Test-Path $Py)) { $Py = 'python' }

function Invoke-Step {
    param([string]$Command)
    Write-Host "> $Command" -ForegroundColor DarkGray
    Invoke-Expression $Command
    if ($LASTEXITCODE -ne 0) {
        throw "step failed (exit $LASTEXITCODE): $Command"
    }
}

switch ($Target) {
    'help' {
        Write-Host ""
        Write-Host "  PartScope targets" -ForegroundColor Cyan
        Write-Host ""
        Write-Host "  setup    Install Python, API and web dependencies"
        Write-Host "  up       Start Postgres + Mongo"
        Write-Host "  down     Stop the databases (data is kept)"
        Write-Host "  seed     Generate the synthetic catalog and price history"
        Write-Host "  reseed   Wipe and regenerate all synthetic data"
        Write-Host "  verify   Prove the seeded data has the properties we claim"
        Write-Host "  dev      Run ml-service, api and web together"
        Write-Host "  test     Run the Python and Node test suites"
        Write-Host "  demo     Databases up, data seeded, everything running"
        Write-Host "  clean    Remove containers and volumes (destroys seeded data)"
        Write-Host ""
    }
    'setup' {
        if (-not (Test-Path '.venv')) { Invoke-Step 'python -m venv .venv' }
        $Py = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
        Invoke-Step "& '$Py' -m pip install --upgrade pip"
        Invoke-Step "& '$Py' -m pip install -r ml-service/requirements.txt"
        Invoke-Step 'npm --prefix api install'
        Invoke-Step 'npm --prefix web install'
    }
    'up' {
        Invoke-Step 'docker compose up -d'
        Invoke-Step "& '$Py' scripts/wait_for_db.py"
    }
    'down'   { Invoke-Step 'docker compose down' }
    'seed'   { Invoke-Step "& '$Py' scripts/seed.py" }
    'reseed' { Invoke-Step "& '$Py' scripts/seed.py --force" }
    'verify' { Invoke-Step "& '$Py' scripts/verify_seed.py" }
    'dev'    { Invoke-Step "& '$Py' scripts/dev.py" }
    'test' {
        Invoke-Step "& '$Py' -m pytest -q ml-service/tests"
        Invoke-Step 'npm --prefix api test --silent'
    }
    'demo' {
        Invoke-Step 'docker compose up -d'
        Invoke-Step "& '$Py' scripts/wait_for_db.py"
        Invoke-Step "& '$Py' scripts/seed.py"
        Invoke-Step "& '$Py' scripts/dev.py"
    }
    'clean'  { Invoke-Step 'docker compose down -v' }
    default {
        Write-Host "unknown target: $Target" -ForegroundColor Red
        Write-Host "run ./make.ps1 help"
        exit 1
    }
}
