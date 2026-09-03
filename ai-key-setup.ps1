$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
$OutputEncoding = [System.Text.UTF8Encoding]::new()
[Console]::OutputEncoding = $OutputEncoding
$env:PYTHONUTF8 = '1'

$core = Join-Path $PSScriptRoot 'ai_key_setup.py'
$pythonCommand = $null
$pythonPrefix = @()

$candidates = @()
$launcher = Get-Command py -ErrorAction SilentlyContinue
if ($launcher) {
    $candidates += ,@($launcher.Source, '-3')
}

$launcher = Get-Command python -ErrorAction SilentlyContinue
if ($launcher) {
    $candidates += ,@($launcher.Source)
}

$bundledPython = Join-Path $HOME '.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe'
if (Test-Path -LiteralPath $bundledPython) {
    $candidates += ,@($bundledPython)
}

foreach ($candidate in $candidates) {
    $command = $candidate[0]
    $prefix = @($candidate | Select-Object -Skip 1)
    try {
        & $command @prefix -c 'import sys; raise SystemExit(sys.version_info < (3, 11))' 2>$null
        if ($LASTEXITCODE -eq 0) {
            $pythonCommand = $command
            $pythonPrefix = $prefix
            break
        }
    } catch {
        continue
    }
}

if (-not $pythonCommand) {
    Write-Error '需要 Python 3.11 或更高版本。请先安装 Python，或从 Codex 环境中运行。'
    exit 1
}

& $pythonCommand @pythonPrefix $core @args
exit $LASTEXITCODE
