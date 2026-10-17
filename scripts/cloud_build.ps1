# cloud_build.ps1 - build an engine on a Google Cloud VM instead of this PC.
#
#   pwsh -File scripts/cloud_build.ps1 create -Region eu   # VM + upload scripts + start the build
#   pwsh -File scripts/cloud_build.ps1 status -Region eu   # tail the remote build log
#   pwsh -File scripts/cloud_build.ps1 fetch  -Region eu   # pull the engine into data/osrm-eu-ferry/
#   pwsh -File scripts/cloud_build.ps1 delete -Region eu   # delete the VM (stops billing)
#
# Why: three hard freezes in two nights during long sequential reads of the
# 12 GB extracts inside Docker Desktop. A 64 GB VM builds North America in
# ~45 min and the PC does nothing heavy. Delete the VM when done.
#
# Regions (scripts/build_ferry_engine.py REGIONS): "na" (default) and "eu".
# Europe has 1.8x the major-road ways of North America, so it gets twice the
# RAM (e2-highmem-16, 128 GB, ~$0.85/h) and a bigger disk for the 33 GB extract.

param(
    [ValidateSet('create', 'status', 'fetch', 'delete')][string]$Action = 'status',
    [ValidateSet('na', 'eu')][string]$Region = 'na'
)

$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $PSScriptRoot
$G = "$env:LOCALAPPDATA\Google\Cloud SDK\google-cloud-sdk\bin\gcloud.cmd"
$Zone = 'us-east1-b'
$Key = "$env:USERPROFILE\.ssh\google_compute_engine"

$Spec = @{
    na = @{ Vm = 'my-osrm-build';    Machine = 'e2-highmem-8';  Disk = '250GB'; Name = 'na-ferry' }
    eu = @{ Vm = 'my-osrm-build-eu'; Machine = 'e2-highmem-16'; Disk = '300GB'; Name = 'eu-ferry' }
}[$Region]
$Vm = $Spec.Vm
$Name = $Spec.Name

function G { & $G @args; if ($LASTEXITCODE) { throw "gcloud failed: $args" } }

# gcloud's own ssh/scp wrappers insist on PuTTY on Windows; use the built-in
# OpenSSH with the key registered in the instance metadata instead.
$SshOpts = @('-i', $Key, '-o', 'BatchMode=yes', '-o', 'StrictHostKeyChecking=accept-new', '-o', 'ConnectTimeout=20')
function VmIp { & $G compute instances describe $Vm --zone $Zone --format='value(networkInterfaces[0].accessConfigs[0].natIP)' }
function VmSsh([string]$cmd) { ssh.exe @SshOpts "mhowe@$(VmIp)" $cmd; if ($LASTEXITCODE) { throw "ssh failed" } }
function ScpTo([string]$src, [string]$dst) { scp.exe @SshOpts $src "mhowe@$(VmIp):$dst"; if ($LASTEXITCODE) { throw "scp failed" } }
function ScpFrom([string]$src, [string]$dst) { scp.exe @SshOpts "mhowe@$(VmIp):$src" $dst; if ($LASTEXITCODE) { throw "scp failed" } }

switch ($Action) {
    'create' {
        if (-not (Test-Path $Key)) {
            # gcloud would otherwise prompt for a passphrase interactively.
            # -N "" (PowerShell passes an empty argument). '""' would make the
            # literal two-character passphrase "" and ssh would prompt forever.
            ssh-keygen -t rsa -b 3072 -f $Key -N "" -q
        }
        # osrm-net: the project's "default" network was left half-deleted when
        # compute was turned off, so the VM gets its own auto-mode network
        # (created once: gcloud compute networks create osrm-net --subnet-mode=auto
        #  + a tcp:22 firewall rule on it).
        G compute instances create $Vm --zone $Zone --machine-type $Spec.Machine `
            --network osrm-net `
            --image-family ubuntu-2404-lts-amd64 --image-project ubuntu-os-cloud `
            --boot-disk-size $Spec.Disk --boot-disk-type pd-balanced
        $meta = Join-Path $env:TEMP 'osrm-sshkeys.txt'
        Set-Content $meta ('mhowe:' + (Get-Content "$Key.pub" -Raw).Trim()) -NoNewline
        G compute instances add-metadata $Vm --zone $Zone --metadata-from-file ssh-keys=$meta
        Start-Sleep 40                                   # sshd + guest agent come up
        $bundle = Join-Path $env:TEMP 'my-osrm-bundle.tgz'
        Set-Location $Root
        tar -czf $bundle scripts profiles tools benchmarks docker-compose.yml
        ScpTo $bundle '~/bundle.tgz'
        # REGION rides along in the environment; cloud_remote.sh reads it.
        VmSsh "mkdir -p my-osrm && tar xzf bundle.tgz -C my-osrm && chmod +x my-osrm/scripts/*.sh && (REGION=$Region nohup bash my-osrm/scripts/cloud_remote.sh > build.log 2>&1 &)"
        "started; poll with: pwsh -File scripts/cloud_build.ps1 status -Region $Region"
    }
    'status' {
        VmSsh "grep -E '^==|\[run\]|\[skip\]|\[redo\]|rror|Traceback|ferries:|crossings:|BUILD_DONE' build.log | tail -25; ls BUILD_DONE 2>/dev/null && echo DONE"
    }
    'fetch' {
        $dest = Join-Path $Root "data\osrm-$Name"
        New-Item -ItemType Directory -Force $dest | Out-Null
        ScpFrom '~/engine.tgz' (Join-Path $Root "data\engine-$Name.tgz")
        ScpFrom '~/reports.tgz' (Join-Path $Root "data\reports-$Name.tgz")
        tar -xzf (Join-Path $Root "data\engine-$Name.tgz") -C $dest
        tar -xzf (Join-Path $Root "data\reports-$Name.tgz") -C $Root     # data/build/*, benchmarks/*
        ScpFrom "~/$Name.osm.pbf" (Join-Path $dest "$Name.osm.pbf")   # for debugging
        Get-ChildItem $dest -File | Measure-Object Length -Sum | ForEach-Object { "engine: {0} files, {1:N1} GB" -f $_.Count, ($_.Sum / 1GB) }
    }
    'delete' {
        G compute instances delete $Vm --zone $Zone --quiet
        "deleted $Vm"
    }
}
