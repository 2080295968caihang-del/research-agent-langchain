[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string] $SourcePath,
    [Parameter(Mandatory = $true)]
    [string] $ProjectRoot,
    [string] $ApiUrl = "http://127.0.0.1:8000/api/projects/import-research-agent"
)

$source = Resolve-Path -LiteralPath $SourcePath -ErrorAction Stop
if (-not (Test-Path -LiteralPath $source.Path -PathType Leaf)) {
    throw "Source DOCX does not exist: $SourcePath"
}
if ([IO.Path]::GetExtension($source.Path) -ne ".docx") {
    throw "Source must be a .docx file: $SourcePath"
}

$body = @{
    source_path = $source.Path
    project_root = [IO.Path]::GetFullPath($ProjectRoot)
} | ConvertTo-Json

$result = Invoke-RestMethod -Method Post -Uri $ApiUrl -ContentType "application/json" -Body $body
Write-Output "Project ID: $($result.project_id)"
Write-Output "Source copy: $($result.source_copy)"
Write-Output "Project path: $([IO.Directory]::GetParent([IO.Directory]::GetParent($result.source_copy).FullName).FullName)"
Write-Output "Created nodes: $($result.created_node_count)"
if ($result.warnings) {
    Write-Output "Warnings: $($result.warnings -join '; ')"
}
