# Creates a Hyper-V test VM for the EZWipe boot USB image.
#
# Run in PowerShell as administrator, after enabling Hyper-V:
#   powershell -ExecutionPolicy Bypass -File packaging\hyperv-test-vm.ps1
#
# The VM gets:
#   - the EZWipe ISO as its boot DVD (Generation 1, BIOS boot)
#   - "EZWipe-Test-Target.vhdx" (20 GB): a spare disk to erase, prefilled with test files
#   - "EZWipe-Test-Data.vhdx" (2 GB, exFAT, label EZWIPE-DATA): where certificates are saved
#
# Hyper-V shows disks as plain virtual disks (no NVMe, no ATA Secure Erase), so this
# tests booting, the full-screen interface, drive discovery, protection, overwrite
# profiles (NIST Clear, Quick zero fill, DoD) and certificates. Firmware erase
# methods need real hardware.
param(
    [string]$Iso = "$env:USERPROFILE\Downloads\ezwipe-iso\ezwipe-0.2.0-amd64.hybrid.iso",
    [string]$Name = "EZWipe-Test",
    [string]$Folder = "$env:USERPROFILE\Documents\EZWipe-VM",
    [int64]$MemoryBytes = 1536MB     # this PC has 8 GB; more fails to start when apps are open
)
$ErrorActionPreference = "Stop"

if (-not ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole(
        [Security.Principal.WindowsBuiltInRole]::Administrator)) {
    throw "Run this script in PowerShell as administrator."
}
if (-not (Get-Command New-VM -ErrorAction SilentlyContinue)) {
    throw "Hyper-V is not enabled. Run: Enable-WindowsOptionalFeature -Online -FeatureName Microsoft-Hyper-V -All  then restart."
}
if (-not (Test-Path $Iso)) { throw "ISO not found: $Iso" }
if (Get-VM -Name $Name -ErrorAction SilentlyContinue) {
    throw "A VM named $Name already exists. Remove it in Hyper-V Manager first, or pass -Name."
}
New-Item -ItemType Directory -Force $Folder | Out-Null
$target = Join-Path $Folder "EZWipe-Test-Target.vhdx"
$data = Join-Path $Folder "EZWipe-Test-Data.vhdx"

Write-Host "== Creating the disk to erase (20 GB) with test files"
New-VHD -Path $target -SizeBytes 20GB -Dynamic | Out-Null
$d = Mount-VHD -Path $target -Passthru | Get-Disk
Initialize-Disk -Number $d.Number -PartitionStyle GPT
$v = New-Partition -DiskNumber $d.Number -UseMaximumSize -AssignDriveLetter |
     Format-Volume -FileSystem NTFS -NewFileSystemLabel "CUSTOMER" -Confirm:$false
$root = "$($v.DriveLetter):\"
1..20 | ForEach-Object {
    $bytes = New-Object byte[] (5MB)
    (New-Object System.Random $_).NextBytes($bytes)
    [IO.File]::WriteAllBytes("$root\document-$_.bin", $bytes)
}
"Customer data that EZWipe must erase." | Set-Content "$root\readme.txt"
Dismount-VHD -Path $target

Write-Host "== Creating the EZWIPE-DATA disk (2 GB, exFAT)"
New-VHD -Path $data -SizeBytes 2GB -Dynamic | Out-Null
$d = Mount-VHD -Path $data -Passthru | Get-Disk
Initialize-Disk -Number $d.Number -PartitionStyle MBR
$v = New-Partition -DiskNumber $d.Number -UseMaximumSize -AssignDriveLetter |
     Format-Volume -FileSystem exFAT -NewFileSystemLabel "EZWIPE-DATA" -Confirm:$false
@"
# EZWipe test settings
operator = "Test operator"
organization = "Sunada Recycling"
location = "Hyper-V test"
"@ | Set-Content -Encoding UTF8 "$($v.DriveLetter):\ezwipe.toml"
Dismount-VHD -Path $data

Write-Host "== Creating VM $Name"
New-VM -Name $Name -Generation 1 -MemoryStartupBytes $MemoryBytes -Path $Folder -NoVHD | Out-Null
Set-VMProcessor -VMName $Name -Count 2
Set-VMMemory -VMName $Name -DynamicMemoryEnabled $false
Add-VMHardDiskDrive -VMName $Name -ControllerType IDE -ControllerNumber 0 -ControllerLocation 0 -Path $target
Add-VMHardDiskDrive -VMName $Name -ControllerType IDE -ControllerNumber 0 -ControllerLocation 1 -Path $data
Set-VMDvdDrive -VMName $Name -ControllerNumber 1 -ControllerLocation 0 -Path $Iso
Set-VMBios -VMName $Name -StartupOrder @("CD", "IDE", "LegacyNetworkAdapter", "Floppy")
Set-VM -Name $Name -CheckpointType Disabled -AutomaticStopAction TurnOff

Write-Host "== Starting the VM and opening its screen"
Start-VM -Name $Name
vmconnect.exe localhost $Name
Write-Host ""
Write-Host "Done. EZWipe should appear full screen in the VM window after about a minute."
Write-Host "Expected: the 20 GB disk is listed as ready under NIST Clear; the 2 GB EZWIPE-DATA disk is Protected."
Write-Host "To start over: Stop-VM $Name -TurnOff; Remove-VM $Name -Force; Remove-Item -Recurse '$Folder'"
