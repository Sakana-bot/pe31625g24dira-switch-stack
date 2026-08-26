[CmdletBinding()]
param(
    [string]$OutputDirectory,
    [switch]$CleanOldPackages
)

$ErrorActionPreference = 'Stop'
$deploymentDirectory = Split-Path -Parent $MyInvocation.MyCommand.Path
$projectRoot = Split-Path -Parent $deploymentDirectory
$version = (Get-Content -LiteralPath (Join-Path $projectRoot 'VERSION') -Raw).Trim()
if (-not $OutputDirectory) {
    $OutputDirectory = Join-Path $projectRoot 'artifacts'
}
$OutputDirectory = [System.IO.Path]::GetFullPath($OutputDirectory)
$stageParent = Join-Path ([System.IO.Path]::GetTempPath()) ("pe31625g24dira-kit-" + [guid]::NewGuid().ToString('N'))
$kitName = "pe31625g24dira-deploy-kit-$version"
$stage = Join-Path $stageParent $kitName
$archive = Join-Path $OutputDirectory "$kitName.tar.gz"

try {
    New-Item -ItemType Directory -Path $stage -Force | Out-Null
    New-Item -ItemType Directory -Path $OutputDirectory -Force | Out-Null

    $projectDocuments = @(
        'README.md',
        'install.sh',
        'VERSIONING.md',
        'UI_STYLE_GUIDE.md',
        'FAN_AND_OPTICAL_CONTROL_HANDOFF.md',
        'HARDWARE_IDENTITY.md'
    )
    foreach ($name in $projectDocuments) {
        Copy-Item -LiteralPath (Join-Path $projectRoot $name) -Destination $stage
    }
    Copy-Item -LiteralPath (Join-Path $projectRoot 'VERSION') -Destination $stage

    Copy-Item -LiteralPath $deploymentDirectory -Destination (Join-Path $stage 'deployment') -Recurse

    $smallB0Destination = Join-Path $stage 'platforms\sil001-hw4-b0'
    New-Item -ItemType Directory -Path $smallB0Destination -Force | Out-Null
    Copy-Item -LiteralPath (Join-Path $projectRoot 'webui\reference_original_6x100.cfg') `
        -Destination (Join-Path $smallB0Destination 'fm_platform_attributes.cfg')

    $driverDestination = Join-Path $stage 'driver\fm10k-uio-6.12.101-ies1'
    New-Item -ItemType Directory -Path $driverDestination -Force | Out-Null
    Copy-Item -Path (Join-Path $projectRoot 'driver\fm10k-uio-6.12.101-ies1\*') -Destination $driverDestination -Recurse

    $switchDestination = Join-Path $stage 'switch_service'
    New-Item -ItemType Directory -Path $switchDestination -Force | Out-Null
    $switchFiles = @(
        'pe31625g24dira-board-init.service',
        'pe31625g24dira-board-init.sh',
        'pe31625g24dira-switch.service',
        'pe31625g24dira-switch.tp',
        'pe31625g24dira-fan-dump.tp',
        'pe31625g24dira-fan-init.tp',
        'pe31625g24dira-fan-init.service',
        'pe31625g24dira-fan-pwm-test.tp',
        'pe31625g24dira-queue-fan-init.sh',
        'pe31625g24dira-testpoint-wrapper.sh',
        'pe31625g24dira-verify.tp'
    )
    foreach ($name in $switchFiles) {
        Copy-Item -LiteralPath (Join-Path $projectRoot "switch_service\$name") -Destination $switchDestination
    }

    $webDestination = Join-Path $stage 'webui'
    New-Item -ItemType Directory -Path (Join-Path $webDestination 'static') -Force | Out-Null
    $webFiles = @(
        'app.py',
        'l2_features.py',
        'runtime_state.py',
        'THIRD_PARTY_NOTICES.md',
        'fan-default.json',
        'pe31625g24dira-switch-manager.service',
        'sensors.tp',
        'status.tp',
        'uio_probe.py',
        'uio_watch.py'
    )
    foreach ($name in $webFiles) {
        Copy-Item -LiteralPath (Join-Path $projectRoot "webui\$name") -Destination $webDestination
    }
    Copy-Item -LiteralPath (Join-Path $projectRoot 'VERSION') -Destination $webDestination
    Copy-Item -Path (Join-Path $projectRoot 'webui\static\*') -Destination (Join-Path $webDestination 'static') -Recurse
    $retiredFontDirectory = Join-Path $webDestination 'static\fonts'
    if (Test-Path -LiteralPath $retiredFontDirectory) {
        Remove-Item -LiteralPath $retiredFontDirectory -Recurse -Force
    }

    $releaseManifest = [ordered]@{
        artifact_type = 'deploy-kit'
        version = $version
        release_status = if ($version.EndsWith('-dev')) { 'development' } else { 'stable' }
        embedded_platform_profiles = @('sil001-hw4-b0')
        runtime_required_for_fresh_install = $true
    }
    $releaseManifestJson = $releaseManifest | ConvertTo-Json
    [System.IO.File]::WriteAllText(
        (Join-Path $stage 'RELEASE-MANIFEST.json'),
        $releaseManifestJson + "`n",
        [System.Text.UTF8Encoding]::new($false)
    )

    # A kit may be built from a Windows checkout. Linux shell, DKMS and C
    # sources must retain LF endings regardless of the local Git setting.
    Get-ChildItem -LiteralPath $stage -File -Recurse | Where-Object {
        $_.Extension -in @('.sh', '.c', '.h') -or $_.Name -in @('dkms.conf', 'Makefile')
    } | ForEach-Object {
        $content = [System.IO.File]::ReadAllText($_.FullName).Replace("`r`n", "`n")
        [System.IO.File]::WriteAllText($_.FullName, $content, [System.Text.UTF8Encoding]::new($false))
    }

    $manifestLines = Get-ChildItem -LiteralPath $stage -File -Recurse | ForEach-Object {
        $relative = $_.FullName.Substring($stage.Length + 1).Replace('\', '/')
        $hash = (Get-FileHash -LiteralPath $_.FullName -Algorithm SHA256).Hash.ToLowerInvariant()
        "$hash  $relative"
    }
    [System.IO.File]::WriteAllLines((Join-Path $stage 'KIT-SHA256SUMS'), $manifestLines, [System.Text.UTF8Encoding]::new($false))

    if (Test-Path -LiteralPath $archive) {
        Remove-Item -LiteralPath $archive -Force
    }
    & tar -C $stageParent -czf $archive $kitName
    if ($LASTEXITCODE -ne 0) {
        throw "tar failed with exit code $LASTEXITCODE"
    }
    $archiveHash = (Get-FileHash -LiteralPath $archive -Algorithm SHA256).Hash.ToLowerInvariant()
    $sidecar = "$archive.sha256"
    [System.IO.File]::WriteAllText($sidecar, "$archiveHash  $([System.IO.Path]::GetFileName($archive))`n", [System.Text.UTF8Encoding]::new($false))

    if ($CleanOldPackages) {
        $currentNames = @([System.IO.Path]::GetFileName($archive), [System.IO.Path]::GetFileName($sidecar))
        $oldPackages = Get-ChildItem -LiteralPath $OutputDirectory -File |
            Where-Object {
                ($_.Name -like 'pe31625g24dira-deploy-kit-*' -or
                 $_.Name -like 'pe31625g24dira-full-offline-kit-*' -or
                 $_.Name -like 'fm10k-uio-*.tar.gz') -and
                $_.Name -notin $currentNames
            }
        foreach ($oldPackage in $oldPackages) {
            if ([System.IO.Path]::GetDirectoryName($oldPackage.FullName) -ne $OutputDirectory) {
                throw "Refusing to remove package outside output directory: $($oldPackage.FullName)"
            }
            Remove-Item -LiteralPath $oldPackage.FullName -Force
        }
        Write-Output "Removed $($oldPackages.Count) old deployment-package files from $OutputDirectory"
    }
    Write-Output $archive
    Write-Output $sidecar
}
finally {
    if (Test-Path -LiteralPath $stageParent) {
        $resolvedTemp = [System.IO.Path]::GetFullPath($stageParent)
        $systemTemp = [System.IO.Path]::GetFullPath([System.IO.Path]::GetTempPath())
        if ($resolvedTemp.StartsWith($systemTemp, [System.StringComparison]::OrdinalIgnoreCase)) {
            Remove-Item -LiteralPath $resolvedTemp -Recurse -Force
        }
    }
}
