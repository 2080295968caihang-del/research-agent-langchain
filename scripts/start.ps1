[CmdletBinding()]
param(
    [string] $ProjectRoot,
    [int] $BackendPort = 8000,
    [int] $FrontendPort = 5173
)

$ErrorActionPreference = "Stop"
$appRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot ".."))
$envFile = Join-Path $appRoot ".env"
if (Test-Path -LiteralPath $envFile -PathType Leaf) {
    Get-Content -LiteralPath $envFile | ForEach-Object {
        $line = $_.Trim()
        if ($line -and -not $line.StartsWith("#") -and $line -match '^([^=]+)=(.*)$') {
            $name = $Matches[1].Trim()
            $value = $Matches[2].Trim().Trim('"').Trim("'")
            [Environment]::SetEnvironmentVariable($name, $value, "Process")
        }
    }
}
if (-not $ProjectRoot) {
    $ProjectRoot = "E:\ResearchProjects"
}
$ProjectRoot = [IO.Path]::GetFullPath($ProjectRoot)
New-Item -ItemType Directory -Force -Path $ProjectRoot | Out-Null
$env:APP_ROOT = $appRoot
$env:PROJECTS_ROOT = $ProjectRoot

$backend = $null
$frontend = $null
$npmCommand = if ($IsWindows) { "npm.cmd" } else { "npm" }
try {
    $backend = Start-Process -FilePath "python" -WorkingDirectory (Join-Path $appRoot "backend") -ArgumentList @(
        "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", $BackendPort
    ) -PassThru
    $frontend = Start-Process -FilePath $npmCommand -WorkingDirectory (Join-Path $appRoot "frontend") -ArgumentList @(
        "run", "dev", "--", "--host", "127.0.0.1", "--port", $FrontendPort
    ) -PassThru
    Write-Output "Backend: http://127.0.0.1:$BackendPort"
    Write-Output "Frontend: http://127.0.0.1:$FrontendPort"
    Write-Output "Press Ctrl+C to stop both processes."
    while ($true) {
        if ($backend.HasExited -or $frontend.HasExited) {
            break
        }
        Start-Sleep -Seconds 1
    }
} finally {
    foreach ($process in @($frontend, $backend)) {
        if ($null -ne $process -and -not $process.HasExited) {
            Stop-Process -Id $process.Id -Force -ErrorAction SilentlyContinue
        }
    }
}
