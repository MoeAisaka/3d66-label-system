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
    Assert-NoReparsePoint -Path $PSCommandPath -Label '诊断脚本路径'
    Assert-NoReparsePoint -Path $repoCandidate -Label '代码仓库'
    $repoRoot = (Resolve-Path -LiteralPath $repoCandidate).Path
    $python = Join-Path $repoRoot '.venv\Scripts\python.exe'
    if (-not (Test-Path -LiteralPath $python -PathType Leaf)) {
        throw '仓库内 .venv 不存在，请先运行 scripts\windows\install.ps1。'
    }
    $backend = Join-Path $repoRoot 'backend'
    if ([string]::IsNullOrEmpty($env:PYTHONPATH)) {
        $env:PYTHONPATH = $backend
    }
    else {
        $env:PYTHONPATH = "$backend;$($env:PYTHONPATH)"
    }
    $arguments = @('-X', 'utf8', '-m', 'app.windows_deploy', 'doctor', '--repo-root', $repoRoot)
    if ($PSBoundParameters.ContainsKey('DataDir')) {
        $arguments += @('--data-dir', $DataDir)
    }
    & $python @arguments
    $exitCode = $LASTEXITCODE
    if ($exitCode -ne 0) {
        exit $exitCode
    }
    exit 0
}
catch {
    [Console]::Error.WriteLine("诊断失败：$($_.Exception.Message)")
    exit 1
}
