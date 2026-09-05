# Stage A step 2: launch QEMU unattended install (background, serial to file)
# Usage: powershell -File launch-vm.ps1 <WorkDir> <install|boot>
#
# NOTE: keep this file ASCII-only. PowerShell 5.1 reads BOM-less scripts as
# ANSI/GBK; multibyte UTF-8 comments can swallow quotes and break parsing.
param(
  [Parameter(Mandatory=$true)][string]$WorkDir,
  [Parameter(Mandatory=$true)][ValidateSet('install','boot')][string]$Mode,
  [string]$IsoPath,
  [string]$Accel = 'whpx:tcg'
)
$ErrorActionPreference = 'Stop'
$QEMU = "$env:ProgramFiles\qemu\qemu-system-x86_64.exe"
$log = @()

# Pass args as ONE string: Start-Process array form does not preserve quotes,
# so any value containing spaces must carry literal quotes in the string.
if ($Mode -eq 'install') {
  if (-not $IsoPath) {
    $IsoPath = Join-Path $WorkDir 'debian-13.6.0-amd64-netinst.iso'
  }
  if (-not (Test-Path -LiteralPath $IsoPath -PathType Leaf)) {
    throw "Installer ISO not found: $IsoPath"
  }
  # Direct kernel boot; preseed rides in the appended initrd segment.
  # Keep the ISO attached because d-i still mounts it as installation media.
  # -no-reboot: QEMU exits when d-i reboots after install => our completion signal.
  $qargs = "-m 2048 -smp 2 -machine accel=$Accel " +
    "-drive file=$WorkDir\disk.qcow2,if=virtio,format=qcow2 " +
    "-cdrom `"$IsoPath`" " +
    "-nic user,model=virtio-net-pci " +
    "-display none -monitor none " +
    "-serial file:$WorkDir\serial-$Mode.log " +
    "-kernel $WorkDir\vmlinuz " +
    "-initrd $WorkDir\initrd-vmtest.gz " +
    '-append "auto=true priority=critical preseed/file=/preseed.cfg locale=en_US.UTF-8 console=tty0 console=ttyS0,115200n8 ---" ' +
    "-no-reboot"
} else {
  # Post-install: boot from disk only, verify the installed system comes up.
  $qargs = "-m 2048 -smp 2 -machine accel=$Accel " +
    "-drive file=$WorkDir\disk.qcow2,if=virtio,format=qcow2 " +
    "-display none -monitor none " +
    "-serial file:$WorkDir\serial-$Mode.log " +
    "-boot c -no-reboot"
}

$log += "QEMU args: $qargs"
try {
  # Workaround: this machine has BOTH http_proxy and HTTP_PROXY in the env.
  # Start-Process builds a case-insensitive env dictionary and dies on the
  # duplicate. Drop the lowercase copies in-process before spawning.
  foreach ($n in 'http_proxy','https_proxy','no_proxy','all_proxy') {
    Remove-Item "env:$n" -ErrorAction SilentlyContinue
  }
  $p = Start-Process -FilePath $QEMU -ArgumentList $qargs -PassThru -WindowStyle Hidden `
    -RedirectStandardError "$WorkDir\qemu-$Mode-err.txt" -RedirectStandardOutput "$WorkDir\qemu-$Mode-out.txt"
  Start-Sleep -Seconds 2
  if ($p.HasExited) { $log += "QEMU_EXITED code=$($p.ExitCode)" } else { $log += "PID=$($p.Id) running" }
} catch {
  $log += "START_FAILED: $($_.Exception.Message)"
}
$log | Set-Content -Path "$WorkDir\vm-$Mode.pid.txt" -Encoding UTF8
