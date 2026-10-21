[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string] $ProjectPath,
    [Parameter(Mandatory = $true)]
    [string] $Destination,
    [string] $DatabasePath
)

$ErrorActionPreference = "Stop"

function Get-FullPath([string] $PathValue) {
    $full = [IO.Path]::GetFullPath($PathValue)
    if (Test-Path -LiteralPath $full) {
        return (Resolve-Path -LiteralPath $full).Path
    }
    $parent = Split-Path -Parent $full
    if ($parent -and (Test-Path -LiteralPath $parent)) {
        return Join-Path (Resolve-Path -LiteralPath $parent).Path (Split-Path -Leaf $full)
    }
    return $full
}

function Test-PathWithin([string] $Candidate, [string] $Parent) {
    $relative = [IO.Path]::GetRelativePath($Parent, $Candidate)
    return $relative -eq "." -or (
        -not [IO.Path]::IsPathRooted($relative) -and
        $relative -notmatch '^(?:\.\.[\\/]|\.\.)'
    )
}

function Test-ExcludedName([string] $Name) {
    return $Name -match '^(?:\.env(?:\..*)?|credentials?(?:\..*)?|.*(?:secret|password|token|api[_-]?key|private[_-]?key).*)$'
}

function Import-DotEnv([string] $AppRoot) {
    $envFile = Join-Path $AppRoot ".env"
    if (-not (Test-Path -LiteralPath $envFile -PathType Leaf)) {
        return
    }
    Get-Content -LiteralPath $envFile | ForEach-Object {
        $line = $_.Trim()
        if ($line -and -not $line.StartsWith("#") -and $line -match '^([^=]+)=(.*)$') {
            $name = $Matches[1].Trim()
            $value = $Matches[2].Trim().Trim('"').Trim("'")
            [Environment]::SetEnvironmentVariable($name, $value, "Process")
        }
    }
}

function Copy-AllowedTree([string] $Source, [string] $Stage, [string] $ArchivePrefix, [string] $ProjectRoot) {
    if (-not (Test-Path -LiteralPath $Source -PathType Container)) {
        return
    }
    Get-ChildItem -LiteralPath $Source -File -Recurse -Force | ForEach-Object {
        if (Test-ExcludedName $_.Name) {
            return
        }
        $resolvedFile = (Resolve-Path -LiteralPath $_.FullName).Path
        if (-not (Test-PathWithin $resolvedFile $ProjectRoot)) {
            return
        }
        $relative = [IO.Path]::GetRelativePath($Source, $_.FullName)
        $target = Join-Path (Join-Path $Stage $ArchivePrefix) $relative
        New-Item -ItemType Directory -Force -Path (Split-Path -Parent $target) | Out-Null
        Copy-Item -LiteralPath $resolvedFile -Destination $target -Force
    }
}

$project = Get-FullPath $ProjectPath
if (-not (Test-Path -LiteralPath $project -PathType Container)) {
    throw "Project path does not exist: $ProjectPath"
}
$destination = Get-FullPath $Destination
if (Test-PathWithin $destination $project) {
    throw "Backup destination must be outside the project directory: $Destination"
}
New-Item -ItemType Directory -Force -Path $destination | Out-Null
$appRoot = Get-FullPath (Join-Path $PSScriptRoot "..")
Import-DotEnv $appRoot

foreach ($required in @("project.json", "plan.json")) {
    $requiredPath = Join-Path $project $required
    if (-not (Test-Path -LiteralPath $requiredPath -PathType Leaf)) {
        throw "Project is missing required file: $required"
    }
    $resolvedRequired = (Resolve-Path -LiteralPath $requiredPath).Path
    if (-not (Test-PathWithin $resolvedRequired $project)) {
        throw "Project file resolves outside the project directory: $required"
    }
}

if (-not $DatabasePath) {
    $databaseUrl = $env:DATABASE_URL
    if ($databaseUrl -and $databaseUrl.StartsWith("sqlite:///")) {
        $DatabasePath = $databaseUrl.Substring(10)
        if (-not [IO.Path]::IsPathRooted($DatabasePath)) {
            $DatabasePath = Join-Path $appRoot $DatabasePath
        }
    } else {
        $DatabasePath = Join-Path $appRoot "data\app.db"
    }
}
$database = Get-FullPath $DatabasePath
if (-not (Test-Path -LiteralPath $database -PathType Leaf)) {
    throw "Database file does not exist: $database"
}

$timestamp = Get-Date -Format "yyyyMMdd-HHmmss"
$archive = Join-Path $destination "research-agent-backup-$timestamp.zip"
$stage = Join-Path ([IO.Path]::GetTempPath()) ("research-agent-backup-" + [guid]::NewGuid().ToString("N"))

try {
    $projectStage = Join-Path $stage "project"
    $appStage = Join-Path $stage "_app"
    New-Item -ItemType Directory -Force -Path $projectStage, $appStage | Out-Null
    foreach ($required in @("project.json", "plan.json")) {
        Copy-Item -LiteralPath (Join-Path $project $required) -Destination (Join-Path $projectStage $required)
    }
    foreach ($directory in @("notes", "reports", "source", "attachments", "exports")) {
        Copy-AllowedTree (Join-Path $project $directory) $stage ("project\" + $directory) $project
    }
    $snapshotPath = Join-Path $appStage "app.db"
    $sqliteBackup = @'
import sqlite3
import sys

source, destination = sys.argv[1:3]
with sqlite3.connect(source) as source_db, sqlite3.connect(destination) as destination_db:
    source_db.backup(destination_db)
    if destination_db.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
        raise RuntimeError("SQLite integrity check failed")
'@
    $sqliteBackup | & python - $database $snapshotPath
    if ($LASTEXITCODE -ne 0) {
        throw "SQLite online backup failed for: $database"
    }
    Compress-Archive -Path (Join-Path $stage "*") -DestinationPath $archive -CompressionLevel Optimal
    Write-Output $archive
} finally {
    if (Test-Path -LiteralPath $stage) {
        Remove-Item -LiteralPath $stage -Recurse -Force -ErrorAction SilentlyContinue
    }
}
