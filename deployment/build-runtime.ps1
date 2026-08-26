[CmdletBinding()]
param(
    [string]$SourceBoardBundle,
    [string]$OutputDirectory,
    [switch]$CleanOldPackages
)

$ErrorActionPreference = 'Stop'
$deploymentDirectory = Split-Path -Parent $MyInvocation.MyCommand.Path
$projectRoot = Split-Path -Parent $deploymentDirectory
$defaultBundleDirectory = Join-Path $projectRoot 'backups\board-bundles'
if (-not $SourceBoardBundle) {
    $candidates = @(Get-ChildItem -LiteralPath $defaultBundleDirectory -File `
        -Filter 'pe31625g24dira-board-bundle-*.tar.gz' -ErrorAction SilentlyContinue)
    if ($candidates.Count -ne 1) {
        throw 'Specify -SourceBoardBundle; exactly one validated sil001-hw4-b0 source bundle is required'
    }
    $SourceBoardBundle = $candidates[0].FullName
}
$source = [System.IO.Path]::GetFullPath($SourceBoardBundle)
if (-not (Test-Path -LiteralPath $source -PathType Leaf)) {
    throw "Source board bundle not found: $source"
}
$runtimeVersion = (Get-Content -LiteralPath (Join-Path $deploymentDirectory 'RUNTIME_VERSION') -Raw).Trim()
if ($runtimeVersion -notmatch '^\d+\.\d+\.\d+(?:[-+][A-Za-z0-9._-]+)?$') {
    throw "Invalid runtime package version: $runtimeVersion"
}
if (-not $OutputDirectory) { $OutputDirectory = Join-Path $projectRoot 'artifacts' }
$OutputDirectory = [System.IO.Path]::GetFullPath($OutputDirectory)
$stageParent = Join-Path ([System.IO.Path]::GetTempPath()) ("pe31625g24dira-runtime-" + [guid]::NewGuid().ToString('N'))
$runtimeName = "pe31625g24dira-legacy-sdk-runtime-$runtimeVersion"
$stage = Join-Path $stageParent $runtimeName
$archive = Join-Path $OutputDirectory "$runtimeName.tar.gz"

try {
    New-Item -ItemType Directory -Path $stage -Force | Out-Null
    New-Item -ItemType Directory -Path $OutputDirectory -Force | Out-Null
    $extract = Join-Path $stageParent 'source-audit'
    New-Item -ItemType Directory -Path $extract -Force | Out-Null
    & tar -C $extract -xzf $source
    if ($LASTEXITCODE -ne 0) { throw 'Unable to extract source board bundle' }
    $bundleRoot = Join-Path $extract 'pe31625g24dira-board-bundle'
    $bundleManifest = Join-Path $bundleRoot 'manifest.env'
    $bundleHashes = Join-Path $bundleRoot 'SHA256SUMS'
    if (-not (Test-Path -LiteralPath $bundleManifest -PathType Leaf) -or
        -not (Test-Path -LiteralPath $bundleHashes -PathType Leaf)) {
        throw 'Source board bundle is missing its manifest or hash list'
    }
    $manifestText = Get-Content -LiteralPath $bundleManifest -Raw
    if ($manifestText -notmatch '(?m)^FORMAT_VERSION=3$' -or
        $manifestText -notmatch '(?m)^PRODUCT_MODEL=PE31625G24DIRA$' -or
        $manifestText -notmatch '(?m)^PCI_SUBSYSTEM=0x1374:0x01d0$') {
        throw 'Source board bundle does not match the validated sil001-hw4-b0 profile'
    }
    foreach ($line in Get-Content -LiteralPath $bundleHashes) {
        if ($line -notmatch '^([0-9a-fA-F]{64})  (.+)$') { throw "Invalid source hash entry: $line" }
        $expected = $Matches[1].ToLowerInvariant()
        $relative = $Matches[2] -replace '^\./', ''
        $candidate = [System.IO.Path]::GetFullPath((Join-Path $bundleRoot $relative))
        if (-not $candidate.StartsWith($bundleRoot + [System.IO.Path]::DirectorySeparatorChar,
                [System.StringComparison]::OrdinalIgnoreCase)) {
            throw "Unsafe source path: $relative"
        }
        $actual = (Get-FileHash -LiteralPath $candidate -Algorithm SHA256).Hash.ToLowerInvariant()
        if ($actual -ne $expected) { throw "Source bundle hash mismatch: $relative" }
    }
    if (-not (Test-Path -LiteralPath (Join-Path $bundleRoot 'rootfs\opt\silicom-legacy\usr\local\rrc') -PathType Container)) {
        throw 'Source board bundle does not contain the legacy SDK runtime'
    }

    # The source is a per-board recovery bundle, but the published runtime must
    # be hardware-profile generic. Repackage only the controlled SDK root; do
    # not carry VPD, DMI, serial numbers, OS audit data, or platform files into
    # the runtime artifact.
    $payloadName = 'pe31625g24dira-runtime-rootfs'
    $payloadRoot = Join-Path $stageParent $payloadName
    $payloadOpt = Join-Path $payloadRoot 'opt'
    New-Item -ItemType Directory -Path $payloadOpt -Force | Out-Null
    Copy-Item -LiteralPath (Join-Path $bundleRoot 'rootfs\opt\silicom-legacy') `
        -Destination $payloadOpt -Recurse
    foreach ($stalePlatform in @(
        'fm_platform_attributes.cfg',
        'fm_platform_attributes-A0.cfg',
        'fm_platform_attributes_rev_1.cfg'
    )) {
        $stalePath = Join-Path $payloadOpt "silicom-legacy\usr\local\rrc\$stalePlatform"
        if (Test-Path -LiteralPath $stalePath -PathType Leaf) {
            Remove-Item -LiteralPath $stalePath -Force
        }
    }
    $runtimePayload = Join-Path $stage 'runtime-rootfs.tar.gz'
    & tar -C $stageParent -czf $runtimePayload $payloadName
    if ($LASTEXITCODE -ne 0) { throw 'Unable to create the identity-free runtime payload' }
    $runtimeManifest = [ordered]@{
        artifact_type = 'legacy-sdk-runtime'
        format_version = 2
        package_version = $runtimeVersion
        product_model = 'PE31625G24DIRA'
        sdk_name = 'Intel IES TestPoint'
        sdk_version = '4.3'
        architecture = 'x86_64'
        compatible_platform_profiles = @('sil001-hw4-b0')
        compatible_pci_subsystems = @('1374:01d0')
        payload_layout = 'identity-free-rootfs'
        included_roots = @('/opt/silicom-legacy')
        contains_device_identity = $false
    }
    [System.IO.File]::WriteAllText(
        (Join-Path $stage 'RUNTIME-MANIFEST.json'),
        ($runtimeManifest | ConvertTo-Json -Depth 4) + "`n",
        [System.Text.UTF8Encoding]::new($false)
    )
    $hashLines = @('RUNTIME-MANIFEST.json', 'runtime-rootfs.tar.gz') | ForEach-Object {
        $hash = (Get-FileHash -LiteralPath (Join-Path $stage $_) -Algorithm SHA256).Hash.ToLowerInvariant()
        "$hash  $_"
    }
    [System.IO.File]::WriteAllLines((Join-Path $stage 'RUNTIME-SHA256SUMS'), $hashLines,
        [System.Text.UTF8Encoding]::new($false))

    if (Test-Path -LiteralPath $archive) { Remove-Item -LiteralPath $archive -Force }
    & tar -C $stageParent -czf $archive $runtimeName
    if ($LASTEXITCODE -ne 0) { throw "tar failed with exit code $LASTEXITCODE" }
    $archiveHash = (Get-FileHash -LiteralPath $archive -Algorithm SHA256).Hash.ToLowerInvariant()
    $sidecar = "$archive.sha256"
    [System.IO.File]::WriteAllText($sidecar,
        "$archiveHash  $([System.IO.Path]::GetFileName($archive))`n", [System.Text.UTF8Encoding]::new($false))
    if ($CleanOldPackages) {
        $keep = @([System.IO.Path]::GetFileName($archive), [System.IO.Path]::GetFileName($sidecar))
        Get-ChildItem -LiteralPath $OutputDirectory -File |
            Where-Object { $_.Name -like 'pe31625g24dira-legacy-sdk-runtime-*' -and $_.Name -notin $keep } |
            Remove-Item -Force
    }
    Write-Output $archive
    Write-Output $sidecar
}
finally {
    if (Test-Path -LiteralPath $stageParent) {
        $resolved = [System.IO.Path]::GetFullPath($stageParent)
        $temp = [System.IO.Path]::GetFullPath([System.IO.Path]::GetTempPath())
        if ($resolved.StartsWith($temp, [System.StringComparison]::OrdinalIgnoreCase)) {
            Remove-Item -LiteralPath $resolved -Recurse -Force
        }
    }
}
