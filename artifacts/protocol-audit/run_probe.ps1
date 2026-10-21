# D1 协议审计：库级探针 runner（可重放）
#
# 用法（仓库根目录）：
#   pwsh -NoProfile -File artifacts\protocol-audit\run_probe.ps1
#
# 做法：在隔离模块副本 artifacts/protocol-audit/probe-module 里运行探针包，
# 覆盖 CLI 触发不到的库级路径（append_rows / snapshot_at / create_table）。
# 之所以要副本：仓库 moon.mod `source = "src"` 只索引 src/ 下的包，
# 而 D1 不允许改仓库 src/。

$ErrorActionPreference = 'Continue'
$repo = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
$module = Join-Path $PSScriptRoot 'probe-module'
$outDir = Join-Path $PSScriptRoot 'out'
$exe = Join-Path $repo '_build\native\debug\build\cmd\delta\delta.exe'
New-Item -ItemType Directory -Force $outDir | Out-Null

foreach ($d in @('probe-append-mismatch', 'probe-append-name-mismatch', 'probe-append-column-count',
                 'probe-append-row-lengths', 'probe-append-partition', 'probe-append-notnull',
                 'probe-created-invalid', 'probe-append-decimal', 'probe-append-writer-v5')) {
  Remove-Item -Recurse -Force (Join-Path $PSScriptRoot "fixtures\$d") -ErrorAction SilentlyContinue
}

$commands = New-Object System.Collections.Generic.List[string]
$commands.Add('')
$commands.Add('# ---- 库级探针（隔离模块副本 artifacts/protocol-audit/probe-module）----')

# 1) 探针本体
$probeOut = Join-Path $outDir 'probe-library.out'
$probeErr = Join-Path $outDir 'probe-library.stderr'
Push-Location $module
& moon run --target native src/probe 1> $probeOut 2> $probeErr
$code = $LASTEXITCODE
Pop-Location
$stderr = if (Test-Path $probeErr) { [System.IO.File]::ReadAllText($probeErr) } else { '' }
$header = @()
$header += '### probe-library'
$header += '$ cd artifacts/protocol-audit/probe-module && moon run --target native src/probe'
$header += "[exit code: $code]"
$header += '--- stdout ---'
$body = if (Test-Path $probeOut) { [System.IO.File]::ReadAllText($probeOut) } else { '' }
$header += ($body -replace "`r`n", "`n").TrimEnd()
$header += '--- stderr ---'
$header += ($stderr -replace "`r`n", "`n").TrimEnd()
$header += ''
Set-Content -Path $probeOut -Encoding utf8 -Value ($header -join "`n")
Remove-Item $probeErr -ErrorAction SilentlyContinue
$commands.Add("probe-library : moon run --target native src/probe (cwd=probe-module) => exit $code")
Write-Host "[probe-library] exit $code"

# 2) 用 pyarrow 检查探针写出的 parquet 的 nullability / 物理类型
$py = Join-Path $repo '.tools\pyenv\Scripts\python.exe'
$inspect = Join-Path $PSScriptRoot 'inspect_written.py'
$nullOut = Join-Path $outDir 'probe-written-parquet.out'
$appendOut = Join-Path $outDir 'probe-written-parquet-appended.out'
& $py $inspect `
  "artifacts/protocol-audit/fixtures/probe-append-notnull/*.parquet" `
  "artifacts/protocol-audit/fixtures/probe-append-mismatch/*.parquet" `
  "artifacts/protocol-audit/fixtures/probe-append-decimal/*.parquet" `
  "artifacts/protocol-audit/fixtures/probe-append-writer-v5/*.parquet" `
  1> $nullOut 2>&1
$c2 = $LASTEXITCODE
Set-Content -Path $nullOut -Encoding utf8 -Value (@(
  '### probe-written-parquet'
  '$ .tools\pyenv\Scripts\python.exe artifacts/protocol-audit/inspect_written.py artifacts/protocol-audit/fixtures/probe-*/*.parquet'
  "[exit code: $c2]"
  '--- apache-arrow 读回 moondelta 写出的 parquet schema ---'
  ([System.IO.File]::ReadAllText($nullOut) -replace "`r`n", "`n").TrimEnd()
) -join "`n")
$commands.Add("probe-written-parquet : pyarrow read_schema of probe-*/*.parquet => exit $c2")
Write-Host "[probe-written-parquet] exit $c2"

& $py $inspect `
  "artifacts/protocol-audit/fixtures/appended-flat/*.parquet" `
  1> $appendOut 2>&1
$c3 = $LASTEXITCODE
Set-Content -Path $appendOut -Encoding utf8 -Value (@(
  '### appended-flat-parquet'
  '$ .tools\pyenv\Scripts\python.exe artifacts/protocol-audit/inspect_written.py artifacts/protocol-audit/fixtures/appended-flat/*.parquet'
  "[exit code: $c3]"
  ([System.IO.File]::ReadAllText($appendOut) -replace "`r`n", "`n").TrimEnd()
) -join "`n")
$commands.Add("appended-flat-parquet : pyarrow read_schema of appended-flat/*.parquet => exit $c3")
Write-Host "[appended-flat-parquet] exit $c3"

# 3) CLI 观察探针生成的表（log 层面证据）
function Invoke-ProbeCli {
  param([string]$Name, [string[]]$ArgList)
  $o = Join-Path $outDir "$Name.stdout"
  $e = Join-Path $outDir "$Name.stderr"
  & $exe @ArgList 1> $o 2> $e
  $c = $LASTEXITCODE
  $stdout = if (Test-Path $o) { [System.IO.File]::ReadAllText($o) } else { '' }
  $stderr = if (Test-Path $e) { [System.IO.File]::ReadAllText($e) } else { '' }
  Remove-Item $o, $e -ErrorAction SilentlyContinue
  $block = @(
    "### $Name"
    ("`$ delta.exe " + ($ArgList -join ' '))
    "[exit code: $c]"
    '--- stdout ---'
    (($stdout -replace "`r`n", "`n").TrimEnd())
    '--- stderr ---'
    (($stderr -replace "`r`n", "`n").TrimEnd())
    ''
  )
  Set-Content -Path (Join-Path $outDir "$Name.out") -Encoding utf8 -Value ($block -join "`n")
  $commands.Add("$Name : delta.exe $($ArgList -join ' ') => exit $c")
  Write-Host "[$Name] exit $c"
}

Invoke-ProbeCli 'probe-cli-writer-v5' @('inspect', 'artifacts/protocol-audit/fixtures/probe-append-writer-v5')
Invoke-ProbeCli 'probe-cli-append-mismatch' @('read', 'artifacts/protocol-audit/fixtures/probe-append-mismatch')
Invoke-ProbeCli 'probe-cli-name-mismatch' @('read', 'artifacts/protocol-audit/fixtures/probe-append-name-mismatch')
Invoke-ProbeCli 'probe-cli-notnull' @('read', 'artifacts/protocol-audit/fixtures/probe-append-notnull')
Invoke-ProbeCli 'probe-cli-decimal' @('read', 'artifacts/protocol-audit/fixtures/probe-append-decimal')
Invoke-ProbeCli 'probe-cli-invalid-schema' @('inspect', 'artifacts/protocol-audit/fixtures/probe-created-invalid')

Add-Content -Path (Join-Path $PSScriptRoot 'commands.txt') -Encoding utf8 -Value ($commands -join "`n")
Write-Host 'done'
