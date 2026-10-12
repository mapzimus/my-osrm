# cloud_build.ps1 - build the engine on a Google Cloud VM instead of this PC.
#
#   pwsh -File scripts/cloud_build.ps1 create     # VM + upload scripts + start the build
#   pwsh -File scripts/cloud_build.ps1 status     # tail the remote build log
#   pwsh -File scripts/cloud_build.ps1 fetch      # pull the engine into data/osrm-na-ferry/
#   pwsh -File scripts/cloud_build.ps1 delete     # delete the VM (stops billing)
#
# Why: three hard freezes in two nights during long sequential reads of the
# 12 GB extracts inside Docker Desktop. A 64 GB VM builds in ~45 min and the
# PC does nothing heavy. ~$0.40/h while it exists; delete it when done.

param([ValidateSet('create', 'status', 'fetch', 'delete')][string]$Action = 'status')

$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $PSScriptRoot
$G = "$env:LOCALAPPDATA\Google\Cloud SDK\google-cloud-sdk\bin\gcloud.cmd"
$Vm = 'my-osrm-build'
$Zone = 'us-east1-b'
$Key = "$env:USERPROFILE\.ssh\google_compute_engine"

function G { & $G @args; if ($LASTEXITCODE) { throw "gcloud failed: $args" } }

switch ($Action) {
    'create' {
        if (-not (Test-Path $Key)) {
            # gcloud would otherwise prompt for a passphrase interactively.
            ssh-keygen -t rsa -b 3072 -f $Key -N '""' -q
        }
        G compute instances create $Vm --zone $Zone --machine-type e2-highmem-8 `
            --image-family ubuntu-2404-lts-amd64 --image-project ubuntu-os-cloud `
            --boot-disk-size 250GB --boot-disk-type pd-balanced
        Start-Sleep 40                                   # sshd + guest agent come up
        $bundle = Join-Path $env:TEMP 'my-osrm-bundle.tgz'
        Set-Location $Root
        tar -czf $bundle scripts profiles tools benchmarks docker-compose.yml
        G compute scp $bundle "${Vm}:~/bundle.tgz" --zone $Zone --quiet
        G compute ssh $Vm --zone $Zone --quiet --command `
            "mkdir -p my-osrm && tar xzf bundle.tgz -C my-osrm && chmod +x my-osrm/scripts/*.sh && nohup bash my-osrm/scripts/cloud_remote.sh > build.log 2>&1 &"
        "started; poll with: pwsh -File scripts/cloud_build.ps1 status"
    }
    'status' {
        G compute ssh $Vm --zone $Zone --quiet --command `
            "grep -E '^==|\[run\]|\[skip\]|\[redo\]|rror|Traceback|ferries:|crossings:|BUILD_DONE' build.log | tail -25; ls BUILD_DONE 2>/dev/null && echo DONE"
    }
    'fetch' {
        $dest = Join-Path $Root 'data\osrm-na-ferry'
        New-Item -ItemType Directory -Force $dest | Out-Null
        G compute scp "${Vm}:~/engine.tgz" (Join-Path $Root 'data\engine.tgz') --zone $Zone --quiet
        G compute scp "${Vm}:~/reports.tgz" (Join-Path $Root 'data\reports.tgz') --zone $Zone --quiet
        tar -xzf (Join-Path $Root 'data\engine.tgz') -C $dest
        tar -xzf (Join-Path $Root 'data\reports.tgz') -C (Join-Path $Root 'data')
        Get-ChildItem $dest -File | Measure-Object Length -Sum | ForEach-Object { "engine: {0} files, {1:N1} GB" -f $_.Count, ($_.Sum / 1GB) }
    }
    'delete' {
        G compute instances delete $Vm --zone $Zone --quiet
        "deleted $Vm"
    }
}
