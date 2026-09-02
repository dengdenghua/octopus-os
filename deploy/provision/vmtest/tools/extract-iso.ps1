# Stage A 第 1 步:从 netinst ISO 抽出 vmlinuz 与 initrd.gz
# 用法:powershell -File extract-iso.ps1 <iso路径> <输出目录>
param(
  [Parameter(Mandatory=$true)][string]$IsoPath,
  [Parameter(Mandatory=$true)][string]$OutDir
)
$ErrorActionPreference = 'Stop'
$log = @()
try {
  New-Item -ItemType Directory -Force -Path $OutDir | Out-Null
  $m = Mount-DiskImage -ImagePath $IsoPath -PassThru
  Start-Sleep -Seconds 3
  $v = Get-Volume -DiskImage $m
  $drive = "$($v.DriveLetter):\"
  $log += "MOUNTED $drive"
  $src = Join-Path $drive 'install.amd'
  Copy-Item (Join-Path $src 'vmlinuz') "$OutDir\vmlinuz" -Force
  Copy-Item (Join-Path $src 'initrd.gz') "$OutDir\initrd.gz" -Force
  $log += "COPIED vmlinuz + initrd.gz"
  Dismount-DiskImage -ImagePath $IsoPath | Out-Null
  $log += "DISMOUNTED"
  $log += "OK"
} catch {
  $log += "FAILED: $($_.Exception.Message)"
  try { Dismount-DiskImage -ImagePath $IsoPath | Out-Null } catch {}
}
$log | Set-Content -Path (Join-Path $OutDir 'extract_status.txt') -Encoding UTF8
