param(
    [string]$ShareName = "pra-evidence",
    [string]$SharePath = "D:\PRA_Evidence",
    [string[]]$ChangeAccess = @("Everyone"),
    [string[]]$ReadAccess = @()
)

$ErrorActionPreference = "Stop"

if (-not (Test-Path -LiteralPath $SharePath)) {
    New-Item -ItemType Directory -Path $SharePath | Out-Null
}

$existing = Get-SmbShare -Name $ShareName -ErrorAction SilentlyContinue
if ($null -eq $existing) {
    $params = @{
        Name = $ShareName
        Path = $SharePath
    }
    if ($ChangeAccess.Count -gt 0) {
        $params.ChangeAccess = $ChangeAccess
    }
    if ($ReadAccess.Count -gt 0) {
        $params.ReadAccess = $ReadAccess
    }
    New-SmbShare @params | Out-Null
}

$share = Get-SmbShare -Name $ShareName
$unc = "\\$env:COMPUTERNAME\$ShareName"

[pscustomobject]@{
    ShareName = $share.Name
    SharePath = $share.Path
    UNC = $unc
}
