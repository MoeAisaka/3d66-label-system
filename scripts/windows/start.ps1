[CmdletBinding()]
param(
    [string]$DataDir
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$utf8 = New-Object System.Text.UTF8Encoding($false)
[Console]::InputEncoding = $utf8
[Console]::OutputEncoding = $utf8
$OutputEncoding = $utf8
$env:PYTHONUTF8 = '1'

function Assert-NoReparsePoint {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][string]$Label
    )

    $current = [IO.Path]::GetFullPath($Path)
    while (-not [string]::IsNullOrEmpty($current)) {
        if (Test-Path -LiteralPath $current) {
            $item = Get-Item -LiteralPath $current -Force
            if (($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
                throw "$Label 不得包含符号链接、junction 或其他重解析点：$current"
            }
        }
        $parent = [IO.Path]::GetDirectoryName($current)
        if ([string]::IsNullOrEmpty($parent) -or $parent -eq $current) {
            break
        }
        $current = $parent
    }
}

try {
    $repoCandidate = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..\..'))
    Assert-NoReparsePoint -Path $PSCommandPath -Label '启动脚本路径'
    Assert-NoReparsePoint -Path $repoCandidate -Label '代码仓库'
    $repoRoot = (Resolve-Path -LiteralPath $repoCandidate).Path
    if ($PSBoundParameters.ContainsKey('DataDir')) {
        $env:DATA_DIR = $DataDir
    }
    if ([string]::IsNullOrWhiteSpace($env:APP_HOST)) {
        $env:APP_HOST = '127.0.0.1'
    }

    $doctorArguments = @()
    if ($PSBoundParameters.ContainsKey('DataDir')) {
        $doctorArguments += @('-DataDir', $DataDir)
    }
    & (Join-Path $PSScriptRoot 'doctor.ps1') @doctorArguments
    $doctorExitCode = $LASTEXITCODE
    if ($doctorExitCode -ne 0) {
        [Console]::Error.WriteLine("启动已阻止：doctor 门禁未通过（退出码 $doctorExitCode）。")
        exit $doctorExitCode
    }

    $python = Join-Path $repoRoot '.venv\Scripts\python.exe'
    Push-Location -LiteralPath (Join-Path $repoRoot 'backend')
    try {
        & $python -X utf8 -m app.launcher
        $serviceExitCode = $LASTEXITCODE
    }
    finally {
        Pop-Location
    }
    exit $serviceExitCode
}
catch {
    [Console]::Error.WriteLine("启动失败：$($_.Exception.Message)")
    exit 1
}
