[CmdletBinding()]
param(
    [switch]$NoPause
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

function Write-Step {
    param([string]$Message)
    Write-Host ""
    Write-Host "==> $Message" -ForegroundColor Cyan
}

function Exit-WithPause {
    param(
        [int]$Code = 0
    )
    if (-not $NoPause) {
        Write-Host ""
        Write-Host "Press any key to close..."
        $null = $Host.UI.RawUI.ReadKey("NoEcho,IncludeKeyDown")
    }
    exit $Code
}

$scriptRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $scriptRoot

try {
    Write-Step "Starting GitHub sync"
    Write-Host "Repository: $scriptRoot"

    $null = Get-Command git -ErrorAction Stop
    git rev-parse --is-inside-work-tree | Out-Null

    Write-Step "Checking local tracked changes"
    $unstagedTracked = (git diff --name-only --) | Where-Object { $_.Trim() }
    $stagedTracked = (git diff --cached --name-only --) | Where-Object { $_.Trim() }

    if ($unstagedTracked -or $stagedTracked) {
        Write-Host "Tracked local changes were detected. Sync stopped to avoid overwriting your work." -ForegroundColor Yellow
        if ($stagedTracked) {
            Write-Host ""
            Write-Host "Staged changes:"
            $stagedTracked | ForEach-Object { Write-Host "  [staged] $_" }
        }
        if ($unstagedTracked) {
            Write-Host ""
            Write-Host "Unstaged changes:"
            $unstagedTracked | ForEach-Object { Write-Host "  [modified] $_" }
        }
        Exit-WithPause 1
    }

    $branch = (git rev-parse --abbrev-ref HEAD).Trim()
    if (-not $branch) {
        throw "Could not determine the current branch."
    }

    Write-Step "Fetching latest changes from origin/$branch"
    git fetch origin --prune

    Write-Step "Pulling latest changes with fast-forward only"
    git pull --ff-only origin $branch

    $venvPython = Join-Path $scriptRoot ".venv\Scripts\python.exe"
    if (Test-Path $venvPython) {
        Write-Step "Updating dependencies inside .venv"
        & $venvPython -m pip install -r requirements.txt
    }
    else {
        Write-Host ""
        Write-Host "No local .venv Python was found, so dependency refresh was skipped." -ForegroundColor Yellow
    }

    Write-Step "Sync completed successfully"
    git log -1 --oneline
    Exit-WithPause 0
}
catch {
    Write-Host ""
    Write-Host "Sync failed." -ForegroundColor Red
    Write-Host $_.Exception.Message -ForegroundColor Red
    Exit-WithPause 1
}
