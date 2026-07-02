param(
    [Parameter(Mandatory=$true)][string]$ManifestPath,
    [Parameter(Mandatory=$true)][string]$VsdxPath,
    [Parameter(Mandatory=$true)][string]$OutputDir,
    [string]$PageMode,
    [int]$PageIndex = 0,
    [double]$PageW = 0,
    [double]$PageH = 0,
    [string[]]$ExportFormats = @('png'),
    [string]$PythonExe = 'python',
    [switch]$Visible
)

$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'visio_export_formats.ps1')

function Invoke-PythonChecked([string[]]$Arguments) {
    & $PythonExe @Arguments
    if ($LASTEXITCODE -ne 0) { throw "Python command failed with exit code ${LASTEXITCODE}: $($Arguments -join ' ')" }
}

function Copy-OutputFile([string]$Source, [string]$Destination) {
    if (Test-Path -LiteralPath $Source) {
        $parent = Split-Path -Parent $Destination
        if (-not (Test-Path -LiteralPath $parent)) { New-Item -ItemType Directory -Force -Path $parent | Out-Null }
        Copy-Item -LiteralPath $Source -Destination $Destination -Force
    }
}

$manifestFull = [IO.Path]::GetFullPath($ManifestPath)
$targetFull = [IO.Path]::GetFullPath($VsdxPath)
$outputFull = [IO.Path]::GetFullPath($OutputDir)
if (-not (Test-Path -LiteralPath $manifestFull)) { throw "Manifest not found: $manifestFull" }
if (-not (Test-Path -LiteralPath $outputFull)) { New-Item -ItemType Directory -Force -Path $outputFull | Out-Null }
$targetDir = Split-Path -Parent $targetFull
if (-not (Test-Path -LiteralPath $targetDir)) { New-Item -ItemType Directory -Force -Path $targetDir | Out-Null }

$buildId = [guid]::NewGuid().ToString('N')
$buildDir = Join-Path ([IO.Path]::GetTempPath()) "visio-manifest-$buildId"
$preparedDir = Join-Path $buildDir 'prepared'
$auditDir = Join-Path $buildDir 'audit'
$exportDir = Join-Path $buildDir 'exports'
$workingVsdx = Join-Path $targetDir ('.' + [IO.Path]::GetFileNameWithoutExtension($targetFull) + ".working-$buildId.vsdx")
$renderReport = Join-Path $buildDir 'render_report.json'
$backupPath = $null
$committed = $false

try {
    New-Item -ItemType Directory -Force -Path $preparedDir,$auditDir,$exportDir | Out-Null
    Invoke-PythonChecked @((Join-Path $PSScriptRoot 'prepare_visio_manifest.py'), $manifestFull, '--out', $preparedDir)
    $preparedManifest = Join-Path $preparedDir 'manifest.json'
    $manifest = Get-Content -Raw -LiteralPath $preparedManifest -Encoding UTF8 | ConvertFrom-Json

    if (-not $PageMode) { $PageMode = if ($manifest.visio.page_mode) { [string]$manifest.visio.page_mode } else { 'replace' } }
    if ($PageMode -notin @('replace','append','new-page')) { throw "Invalid PageMode: $PageMode" }
    if ($PageIndex -le 0) { $PageIndex = if ($manifest.visio.page_index) { [int]$manifest.visio.page_index } else { 1 } }
    if ($PageW -le 0 -and $manifest.visio.page_width_in) { $PageW = [double]$manifest.visio.page_width_in }
    if ($PageH -le 0 -and $manifest.visio.page_height_in) { $PageH = [double]$manifest.visio.page_height_in }

    if (Test-Path -LiteralPath $targetFull) {
        $stem = [IO.Path]::GetFileNameWithoutExtension($targetFull)
        $backupPath = Join-Path $targetDir ($stem + '.backup-' + (Get-Date -Format 'yyyyMMdd-HHmmss') + '.vsdx')
        Copy-Item -LiteralPath $targetFull -Destination $backupPath
        Copy-Item -LiteralPath $targetFull -Destination $workingVsdx
        Write-Output "Backup: $backupPath"
    }

    $rendererArgs = @(
        '-ExecutionPolicy','Bypass','-File',(Join-Path $PSScriptRoot 'visio_manifest_renderer.ps1'),
        '-ManifestPath',$preparedManifest,'-VsdxPath',$workingVsdx,'-RenderReportPath',$renderReport,
        '-PageMode',$PageMode,'-PageIndex',[string]$PageIndex
    )
    if ($PageW -gt 0) { $rendererArgs += @('-PageW',[string]$PageW) }
    if ($PageH -gt 0) { $rendererArgs += @('-PageH',[string]$PageH) }
    if ($Visible) { $rendererArgs += '-Visible' }
    & powershell @rendererArgs
    if ($LASTEXITCODE -ne 0) { throw "Visio manifest renderer failed with exit code $LASTEXITCODE" }
    if (-not (Test-Path -LiteralPath $workingVsdx) -or (Get-Item -LiteralPath $workingVsdx).Length -eq 0) { throw 'Renderer did not create a non-empty VSDX.' }
    $renderState = Get-Content -Raw -LiteralPath $renderReport -Encoding UTF8 | ConvertFrom-Json
    $renderedPageIndex = if ($renderState.page_index) { [int]$renderState.page_index } else { $PageIndex }

    $formats = New-Object System.Collections.Generic.List[string]
    foreach ($format in (Normalize-VisioExportFormats $ExportFormats)) { if (-not $formats.Contains($format)) { $formats.Add($format) } }
    if (-not $formats.Contains('png')) { $formats.Insert(0, 'png') }
    $baseName = [IO.Path]::GetFileNameWithoutExtension($targetFull)
    $previewPath = Join-Path $exportDir "$baseName.png"
    Export-VisioDocumentFormats -VsdxPath $workingVsdx -Formats @($formats) -OutputDir $exportDir -OutputBaseName $baseName -PageIndex $renderedPageIndex -PreviewPath $previewPath -Visible:$Visible
    foreach ($format in $formats) {
        $expected = Join-Path $exportDir "$baseName.$format"
        if (-not (Test-Path -LiteralPath $expected) -or (Get-Item -LiteralPath $expected).Length -eq 0) { throw "Export is missing or empty: $expected" }
    }

    Invoke-PythonChecked @((Join-Path $PSScriptRoot 'audit_visio_package.py'), $preparedManifest, '--vsdx', $workingVsdx, '--render-report', $renderReport, '--out', $auditDir)

    if (Test-Path -LiteralPath $targetFull) {
        $replaceBackup = "$targetFull.replace-$buildId.bak"
        [IO.File]::Replace($workingVsdx, $targetFull, $replaceBackup)
        Remove-Item -LiteralPath $replaceBackup -Force -ErrorAction SilentlyContinue
    } else {
        [IO.File]::Move($workingVsdx, $targetFull)
    }
    $committed = $true

    Copy-OutputFile $preparedManifest (Join-Path $outputFull 'manifest.json')
    Copy-OutputFile (Join-Path $preparedDir 'contact_sheet.png') (Join-Path $outputFull 'contact_sheet.png')
    Copy-OutputFile $renderReport (Join-Path $outputFull 'render_report.json')
    foreach ($name in 'quality_report.json','quality_report.md','editability_report.md') { Copy-OutputFile (Join-Path $auditDir $name) (Join-Path $outputFull $name) }
    foreach ($name in 'ocr_results.json','detected_primitives.json','style_tokens.json','measurement_report.md') { Copy-OutputFile (Join-Path $preparedDir $name) (Join-Path $outputFull $name) }
    foreach ($folder in 'assets','formulas','diagnostics') {
        $source = Join-Path $preparedDir $folder
        if (Test-Path -LiteralPath $source) { Copy-Item -LiteralPath $source -Destination (Join-Path $outputFull $folder) -Recurse -Force }
    }
    Get-ChildItem -LiteralPath $exportDir -File | ForEach-Object { Copy-Item -LiteralPath $_.FullName -Destination (Join-Path $outputFull $_.Name) -Force }

    Write-Output "Saved: $targetFull"
    if ($backupPath) { Write-Output "Backup: $backupPath" }
    Write-Output "Preview: $(Join-Path $outputFull "$baseName.png")"
    Write-Output "Quality report: $(Join-Path $outputFull 'quality_report.md')"
} finally {
    if (-not $committed -and (Test-Path -LiteralPath $workingVsdx)) { Remove-Item -LiteralPath $workingVsdx -Force -ErrorAction SilentlyContinue }
    if (Test-Path -LiteralPath $buildDir) { Remove-Item -LiteralPath $buildDir -Recurse -Force -ErrorAction SilentlyContinue }
}
