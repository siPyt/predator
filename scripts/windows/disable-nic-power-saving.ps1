# Run from an ELEVATED PowerShell:  .\disable-nic-power-saving.ps1 -Adapter "Ethernet 2"
# Turns off the Realtek USB adapter power-saving features (Energy-Efficient Ethernet, Green Ethernet,
# Idle Power Saving) that can make the link to the Pi drop for ~2 seconds when traffic goes idle -> busy.
# The original values are saved to nic-before.json next to this script (all three were 1 = enabled);
# to undo, set the same RegistryKeyword values back to 1 with Set-NetAdapterAdvancedProperty.
param([string]$Adapter = 'Ethernet 2')

$keys = '*EEE', 'EnableGreenEthernet', 'EnableExtraPowerSaving'
$backup = Join-Path $PSScriptRoot 'nic-before.json'

Get-NetAdapterAdvancedProperty -Name $Adapter | Where-Object { $keys -contains $_.RegistryKeyword } |
    Select-Object DisplayName, RegistryKeyword, DisplayValue, @{n = 'RegistryValue'; e = { $_.RegistryValue[0] } } |
    ConvertTo-Json | Out-File $backup

foreach ($k in $keys) {
    Set-NetAdapterAdvancedProperty -Name $Adapter -RegistryKeyword $k -RegistryValue 0
}
Start-Sleep 8   # the adapter resets itself when the settings change
Get-NetAdapterAdvancedProperty -Name $Adapter | Where-Object { $keys -contains $_.RegistryKeyword } |
    Select-Object DisplayName, DisplayValue | Format-Table -Auto
Get-NetAdapter -Name $Adapter | Select-Object Name, Status, LinkSpeed | Format-Table -Auto
