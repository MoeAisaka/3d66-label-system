[CmdletBinding()]
param(
    [switch]$Check,
    [switch]$DryRun
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

function Assert-NoReparseTree {
    param(
        [Parameter(Mandatory = $true)][string]$Root,
        [Parameter(Mandatory = $true)][string]$Label
    )

    Assert-NoReparsePoint -Path $Root -Label $Label
    if (-not (Test-Path -LiteralPath $Root -PathType Container)) {
        return
    }
    $pending = New-Object System.Collections.Stack
    $pending.Push([IO.Path]::GetFullPath($Root))
    while ($pending.Count -gt 0) {
        $current = $pending.Pop()
        foreach ($item in Get-ChildItem -LiteralPath $current -Force) {
            if (($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
                throw "$Label 不得包含符号链接、junction 或其他重解析点：$($item.FullName)"
            }
            if ($item.PSIsContainer) {
                $pending.Push($item.FullName)
            }
        }
    }
}

function Invoke-NativeCapture {
    param(
        [Parameter(Mandatory = $true)][string]$FilePath,
        [Parameter(Mandatory = $true)][object[]]$Arguments
    )

    $output = & $FilePath @Arguments 2>&1
    $exitCode = $LASTEXITCODE
    if ($exitCode -ne 0) {
        throw "子进程失败（退出码 $exitCode）：$FilePath"
    }
    return (($output | Out-String).Trim())
}

function Get-PythonCandidate {
    param([Parameter(Mandatory = $true)][string]$RepositoryRoot)

    $venvPython = Join-Path $RepositoryRoot '.venv\Scripts\python.exe'
    if (Test-Path -LiteralPath $venvPython -PathType Leaf) {
        return [PSCustomObject]@{ FilePath = $venvPython; Prefix = @() }
    }

    $candidateSpecs = @(
        [PSCustomObject]@{ Command = 'py.exe'; Prefix = @('-3.12') },
        [PSCustomObject]@{ Command = 'py.exe'; Prefix = @('-3.11') },
        [PSCustomObject]@{ Command = 'python.exe'; Prefix = @() },
        [PSCustomObject]@{ Command = 'python3.exe'; Prefix = @() }
    )
    foreach ($candidateSpec in $candidateSpecs) {
        $command = Get-Command $candidateSpec.Command -CommandType Application -ErrorAction SilentlyContinue
        if ($null -eq $command) {
            continue
        }
        $versionArguments = @($candidateSpec.Prefix) + @(
            '-X', 'utf8', '-c',
            'import sys; print(".".join(map(str, sys.version_info[:3]))); raise SystemExit(0 if (3, 11) <= sys.version_info[:2] < (3, 13) else 1)'
        )
        try {
            [void](Invoke-NativeCapture -FilePath $command.Source -Arguments $versionArguments)
            return [PSCustomObject]@{
                FilePath = $command.Source
                Prefix = @($candidateSpec.Prefix)
            }
        }
        catch {
            continue
        }
    }
    throw '未检测到可运行的 Python。请安装 Python 3.11 或 3.12 后重试。'
}

try {
    if ($Check -and $DryRun) {
        throw '-Check 与 -DryRun 不能同时使用。'
    }

    $repoCandidate = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..\..'))
    Assert-NoReparsePoint -Path $PSCommandPath -Label '安装脚本路径'
    Assert-NoReparsePoint -Path $repoCandidate -Label '代码仓库'
    $repoRoot = (Resolve-Path -LiteralPath $repoCandidate).Path
    $venvRoot = Join-Path $repoRoot '.venv'
    Assert-NoReparseTree -Root $venvRoot -Label '仓库内 .venv'
    $requirements = Join-Path $repoRoot 'backend\requirements.txt'
    $packageLock = Join-Path $repoRoot 'frontend\package-lock.json'
    $frontendRoot = Join-Path $repoRoot 'frontend'
    Assert-NoReparsePoint -Path $requirements -Label 'Python requirements'
    Assert-NoReparsePoint -Path $packageLock -Label '前端锁文件'
    Assert-NoReparseTree -Root (Join-Path $frontendRoot 'node_modules') -Label 'node_modules'
    if (-not (Test-Path -LiteralPath $requirements -PathType Leaf)) {
        throw "仓库缺少依赖清单：$requirements"
    }
    if (-not (Test-Path -LiteralPath $packageLock -PathType Leaf)) {
        throw "仓库缺少锁文件：$packageLock"
    }

    $python = Get-PythonCandidate -RepositoryRoot $repoRoot
    $pythonVersionArguments = @($python.Prefix) + @(
        '-X', 'utf8', '-c',
        'import sys; print(".".join(map(str, sys.version_info[:3]))); raise SystemExit(0 if (3, 11) <= sys.version_info[:2] < (3, 13) else 1)'
    )
    try {
        $pythonVersion = Invoke-NativeCapture -FilePath $python.FilePath -Arguments $pythonVersionArguments
    }
    catch {
        throw 'Python 版本门禁未通过：仅允许 Python 3.11 或 3.12。'
    }

    $nodeCommand = Get-Command 'node.exe' -CommandType Application -ErrorAction SilentlyContinue
    $npmCommand = Get-Command 'npm.cmd' -CommandType Application -ErrorAction SilentlyContinue
    if ($null -eq $nodeCommand) {
        throw '未检测到 Node.js；请安装 20.x 至 26.x 后重试。'
    }
    if ($null -eq $npmCommand) {
        throw '未检测到 npm；请安装 npm 10.x 或 11.x 后重试。'
    }
    $nodeVersion = Invoke-NativeCapture -FilePath $nodeCommand.Source -Arguments @('--version')
    $npmVersion = Invoke-NativeCapture -FilePath $npmCommand.Source -Arguments @('--version')
    if ($nodeVersion -notmatch '^v?(\d+)\.') {
        throw '无法解析 Node.js 版本。'
    }
    $nodeMajor = [int]$Matches[1]
    if ($nodeMajor -lt 20 -or $nodeMajor -gt 26) {
        throw 'Node.js 版本门禁未通过：仅允许 20.x 至 26.x。'
    }
    if ($npmVersion -notmatch '^(\d+)\.') {
        throw '无法解析 npm 版本。'
    }
    $npmMajor = [int]$Matches[1]
    if ($npmMajor -lt 10 -or $npmMajor -gt 11) {
        throw 'npm 版本门禁未通过：仅允许 10.x 或 11.x。'
    }

    Write-Host "[OK] Python $pythonVersion"
    Write-Host "[OK] Node.js $nodeVersion"
    Write-Host "[OK] npm $npmVersion"
    Write-Host '[OK] 门禁：Python 3.11/3.12；Node.js 20-26；npm 10/11'

    $venvPython = Join-Path $repoRoot '.venv\Scripts\python.exe'
    $frontendIndex = Join-Path $repoRoot 'frontend\dist\index.html'
    if ($Check) {
        if (-not (Test-Path -LiteralPath $venvPython -PathType Leaf)) {
            throw '仓库内 .venv 尚未创建。'
        }
        if (-not (Test-Path -LiteralPath $frontendIndex -PathType Leaf)) {
            throw '前端生产构建不存在。'
        }
        Write-Host '安装状态检查通过；未安装、未构建、未访问网络。'
        exit 0
    }
    if ($DryRun) {
        Write-Host "[DRY-RUN] 创建仓库内虚拟环境（仅在不存在时）：$venvPython"
        Write-Host "[DRY-RUN] 使用 requirements 安装后端依赖：$requirements"
        Write-Host "[DRY-RUN] 在 frontend 中执行 npm ci 与 npm run build"
        Write-Host '未创建文件、未安装、未构建、未访问网络。'
        exit 0
    }

    if (-not (Test-Path -LiteralPath $venvPython -PathType Leaf)) {
        $venvArguments = @($python.Prefix) + @('-X', 'utf8', '-m', 'venv', $venvRoot)
        & $python.FilePath @venvArguments
        if ($LASTEXITCODE -ne 0) {
            throw "创建仓库内 .venv 失败（退出码 $LASTEXITCODE）。"
        }
        Assert-NoReparseTree -Root $venvRoot -Label '仓库内 .venv'
    }

    & $venvPython -X utf8 -m pip install -r $requirements
    if ($LASTEXITCODE -ne 0) {
        throw "安装 Python 依赖失败（退出码 $LASTEXITCODE）。"
    }

    Push-Location -LiteralPath $frontendRoot
    try {
        & $npmCommand.Source ci
        if ($LASTEXITCODE -ne 0) {
            throw "npm ci 失败（退出码 $LASTEXITCODE）。"
        }
        & $npmCommand.Source run build
        if ($LASTEXITCODE -ne 0) {
            throw "前端生产构建失败（退出码 $LASTEXITCODE）。"
        }
    }
    finally {
        Pop-Location
    }
    Write-Host '安装完成。未创建或覆盖任何 DATA_DIR 业务数据，也未启动服务。'
    exit 0
}
catch {
    [Console]::Error.WriteLine("安装失败：$($_.Exception.Message)")
    exit 1
}
