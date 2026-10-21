# D1 协议审计：CLI 实测 runner（可重放）
#
# 用法（仓库根目录）：
#   pwsh -NoProfile -File artifacts\protocol-audit\run_cli.ps1
#
# 输出：
#   artifacts/protocol-audit/out/<case>.out  —— 每条命令的原始 stdout/stderr + 退出码
#   artifacts/protocol-audit/commands.txt     —— 命令清单（含退出码）
#
# 说明：本脚本只读仓库 fixtures、只在 artifacts/protocol-audit/fixtures 下写测试表。

$ErrorActionPreference = 'Continue'
$repo = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
Set-Location $repo

$exe = Join-Path $repo '_build\native\debug\build\cmd\delta\delta.exe'
if (-not (Test-Path $exe)) { throw "CLI not found: $exe" }

$outDir = Join-Path $PSScriptRoot 'out'
$tmpDir = Join-Path $PSScriptRoot 'out\_tmp'
Remove-Item -Recurse -Force $outDir -ErrorAction SilentlyContinue
New-Item -ItemType Directory -Force $outDir | Out-Null
New-Item -ItemType Directory -Force $tmpDir | Out-Null

# 清掉本 runner 生成的可写测试表（只在本审计目录内）
foreach ($d in @('cli-created', 'cli-created-bad', 'cli-nested', 'cli-overwrite',
                 'appended-flat', 'probe-created', 'probe-append-mismatch',
                 'probe-append-partition', 'probe-append-writer-v5',
                 'probe-append-notnull')) {
  Remove-Item -Recurse -Force (Join-Path $PSScriptRoot "fixtures\$d") -ErrorAction SilentlyContinue
}

$commands = New-Object System.Collections.Generic.List[string]
$commands.Add('# D1 协议审计 CLI 命令清单')
$commands.Add("# 生成时间: $(Get-Date -Format o)")
$commands.Add("# CLI: $exe")
$commands.Add('# 每条命令的原始输出见 out/<case>.out；退出码记录在 case 头部的 [exit code: N]')
$commands.Add('')

function Invoke-Case {
  param([string]$Name, [string[]]$ArgList)
  $stdoutFile = Join-Path $tmpDir "$Name.stdout"
  $stderrFile = Join-Path $tmpDir "$Name.stderr"
  Remove-Item $stdoutFile, $stderrFile -ErrorAction SilentlyContinue
  & $exe @ArgList 1> $stdoutFile 2> $stderrFile
  $code = $LASTEXITCODE
  $stdout = if (Test-Path $stdoutFile) { [System.IO.File]::ReadAllText($stdoutFile) } else { '' }
  $stderr = if (Test-Path $stderrFile) { [System.IO.File]::ReadAllText($stderrFile) } else { '' }
  $argv = ($ArgList | ForEach-Object { if ($_ -match '[\s"]') { '"' + ($_ -replace '"', '\"') + '"' } else { $_ } }) -join ' '
  $block = @()
  $block += "### $Name"
  $block += "`$ delta.exe $argv"
  $block += "[exit code: $code]"
  $block += '--- stdout ---'
  $block += ($stdout -replace "`r`n", "`n").TrimEnd()
  $block += '--- stderr ---'
  $block += ($stderr -replace "`r`n", "`n").TrimEnd()
  $block += ''
  $text = $block -join "`n"
  Set-Content -Path (Join-Path $outDir "$Name.out") -Encoding utf8 -Value $text
  $commands.Add("$Name : delta.exe $argv  => exit $code")
  Write-Host ("[{0}] exit {1}" -f $Name, $code)
  return $code
}

function Invoke-External {
  param([string]$Name, [string]$Exe, [string[]]$ArgList, [string]$Label)
  $stdoutFile = Join-Path $tmpDir "$Name.stdout"
  $stderrFile = Join-Path $tmpDir "$Name.stderr"
  & $Exe @ArgList 1> $stdoutFile 2> $stderrFile
  $code = $LASTEXITCODE
  $stdout = if (Test-Path $stdoutFile) { [System.IO.File]::ReadAllText($stdoutFile) } else { '' }
  $stderr = if (Test-Path $stderrFile) { [System.IO.File]::ReadAllText($stderrFile) } else { '' }
  $block = @()
  $block += "### $Name"
  $block += "`$ $Label"
  $block += "[exit code: $code]"
  $block += '--- stdout ---'
  $block += ($stdout -replace "`r`n", "`n").TrimEnd()
  $block += '--- stderr ---'
  $block += ($stderr -replace "`r`n", "`n").TrimEnd()
  $block += ''
  Set-Content -Path (Join-Path $outDir "$Name.out") -Encoding utf8 -Value ($block -join "`n")
  $commands.Add("$Name : $Label => exit $code")
  Write-Host ("[{0}] exit {1}" -f $Name, $code)
  return $code
}

$F = 'artifacts/protocol-audit/fixtures'

# ---------------------------------------------------------------- 内置 fixtures
Invoke-Case '01-version' @('version')
Invoke-Case '02-inspect-table-a' @('inspect', 'fixtures/delta/table_a')
Invoke-Case '03-history-table-a' @('history', 'fixtures/delta/table_a')
Invoke-Case '04-scan-table-a' @('scan', 'fixtures/delta/table_a')
Invoke-Case '05-scan-table-a-id-ge-60' @('scan', 'fixtures/delta/table_a', '--where', 'id>=60')
Invoke-Case '06-scan-table-a-id-eq-7' @('scan', 'fixtures/delta/table_a', '--where', 'id=7')
Invoke-Case '07-scan-table-a-fake-partition' @('scan', 'fixtures/delta/table_a', '--partition', 'id=7')
Invoke-Case '08-inspect-covid' @('inspect', 'fixtures/delta/covid')
Invoke-Case '09-history-covid' @('history', 'fixtures/delta/covid')
Invoke-Case '10-scan-covid' @('scan', 'fixtures/delta/covid')
Invoke-Case '11-read-covid-limit5' @('read', 'fixtures/delta/covid', '--limit', '5')
Invoke-Case '12-read-covid-default-limit' @('read', 'fixtures/delta/covid')
Invoke-Case '13-read-covid-limit0' @('read', 'fixtures/delta/covid', '--limit', '0')
Invoke-Case '14-scan-covid-date-where' @('scan', 'fixtures/delta/covid', '--where', 'date>=2020-05-01')
Invoke-Case '15-read-table-a-missing-data' @('read', 'fixtures/delta/table_a', '--limit', '1')
Invoke-Case '16-inspect-parquet-file-not-table' @('inspect', 'fixtures/parquet/list_columns.parquet')
Invoke-Case '17-inspect-nonexistent' @('inspect', 'artifacts/protocol-audit/fixtures/does-not-exist')
Invoke-Case '18-unknown-subcommand' @('frobnicate')

# ---------------------------------------------------------------- protocol
Invoke-Case '20-inspect-reader-v2' @('inspect', "$F/protocol-reader-v2")
Invoke-Case '21-read-reader-v2' @('read', "$F/protocol-reader-v2")
Invoke-Case '22-inspect-reader-v3-features' @('inspect', "$F/protocol-reader-v3-features")
Invoke-Case '23-read-reader-v3-features' @('read', "$F/protocol-reader-v3-features")
Invoke-Case '24-inspect-no-protocol' @('inspect', "$F/no-protocol")
Invoke-Case '25-read-no-protocol' @('read', "$F/no-protocol")
Invoke-Case '26-inspect-protocol-missing-reader' @('inspect', "$F/protocol-missing-reader")
Invoke-Case '27-inspect-protocol-string-version' @('inspect', "$F/protocol-string-version")
Invoke-Case '28-inspect-writer-v5' @('inspect', "$F/writer-v5-features")
Invoke-Case '29-read-writer-v5' @('read', "$F/writer-v5-features")
Invoke-Case '30-inspect-empty-log' @('inspect', "$F/empty-log")
Invoke-Case '31-read-empty-log' @('read', "$F/empty-log")

# ---------------------------------------------------------------- actions / log
Invoke-Case '32-inspect-unknown-actions' @('inspect', "$F/unknown-actions")
Invoke-Case '33-history-unknown-actions' @('history', "$F/unknown-actions")
Invoke-Case '34-read-unknown-actions' @('read', "$F/unknown-actions")
Invoke-Case '35-inspect-deletion-vector' @('inspect', "$F/deletion-vector-add")
Invoke-Case '36-read-deletion-vector' @('read', "$F/deletion-vector-add")
Invoke-Case '37-inspect-column-mapping' @('inspect', "$F/column-mapping")
Invoke-Case '38-read-column-mapping' @('read', "$F/column-mapping")
Invoke-Case '39-inspect-cdf-enabled' @('inspect', "$F/cdf-enabled")
Invoke-Case '40-inspect-log-gap' @('inspect', "$F/log-gap")
Invoke-Case '41-inspect-log-start-v1' @('inspect', "$F/log-start-v1")
Invoke-Case '42-inspect-nonstandard-name' @('inspect', "$F/nonstandard-log-name")
Invoke-Case '43-inspect-malformed-json' @('inspect', "$F/malformed-json")
Invoke-Case '44-inspect-add-missing-path' @('inspect', "$F/add-missing-path")
Invoke-Case '45-inspect-no-commitinfo' @('inspect', "$F/no-commitinfo")
Invoke-Case '46-history-no-commitinfo' @('history', "$F/no-commitinfo")
Invoke-Case '47-inspect-dup-add-remove' @('inspect', "$F/dup-add-remove")
Invoke-Case '48-history-dup-add-remove' @('history', "$F/dup-add-remove")
Invoke-Case '49-read-dup-add-remove' @('read', "$F/dup-add-remove")

# ---------------------------------------------------------------- checkpoint
Invoke-Case '50-inspect-checkpoint-only' @('inspect', "$F/checkpoint-only")
Invoke-Case '51-inspect-checkpoint-truncated' @('inspect', "$F/checkpoint-truncated")
Invoke-Case '52-history-checkpoint-truncated' @('history', "$F/checkpoint-truncated")
Invoke-Case '53-inspect-bad-last-checkpoint' @('inspect', "$F/bad-last-checkpoint")

# ---------------------------------------------------------------- schema
Invoke-Case '60-inspect-decimal-schema' @('inspect', "$F/decimal-schema")
Invoke-Case '61-read-decimal-schema' @('read', "$F/decimal-schema")
Invoke-Case '62-read-decimal-missing-file' @('read', "$F/decimal-missing-file")
Invoke-Case '63-inspect-uuid-schema-unknown' @('inspect', "$F/uuid-schema-unknown")
Invoke-Case '64-read-uuid-schema-unknown' @('read', "$F/uuid-schema-unknown")
Invoke-Case '65-inspect-uuid-parquet-logical' @('inspect', "$F/uuid-parquet-logical")
Invoke-Case '66-read-uuid-parquet-logical' @('read', "$F/uuid-parquet-logical")
Invoke-Case '67-inspect-nested-struct' @('inspect', "$F/nested-struct")
Invoke-Case '68-read-nested-struct' @('read', "$F/nested-struct")
Invoke-Case '69-inspect-nested-list' @('inspect', "$F/nested-list")
Invoke-Case '70-read-nested-list' @('read', "$F/nested-list")
Invoke-Case '71-inspect-nested-map' @('inspect', "$F/nested-map")
Invoke-Case '72-read-nested-map' @('read', "$F/nested-map")
Invoke-Case '73-read-nested-missing-file' @('read', "$F/nested-missing-file")
Invoke-Case '74-scan-nested-list' @('scan', "$F/nested-list")
Invoke-Case '75-inspect-nested-parquet-lie' @('inspect', "$F/nested-parquet-lie")
Invoke-Case '76-read-nested-parquet-lie' @('read', "$F/nested-parquet-lie")

# ---------------------------------------------------------------- parquet
Invoke-Case '80-inspect-int96-timestamp' @('inspect', "$F/int96-timestamp")
Invoke-Case '81-read-int96-timestamp' @('read', "$F/int96-timestamp")
Invoke-Case '82-read-date-col' @('read', "$F/date-col")
Invoke-Case '83-read-float-col' @('read', "$F/float-col")
foreach ($codec in @('zstd', 'gzip', 'brotli', 'lz4')) {
  Invoke-Case "84-read-codec-$codec" @('read', "$F/codec-$codec")
}
Invoke-Case '85-read-corrupt-parquet' @('read', "$F/corrupt-parquet")
Invoke-Case '86-read-missing-data-file' @('read', "$F/missing-data-file")
Invoke-Case '87-read-type-mismatch' @('read', "$F/type-mismatch")
Invoke-Case '88-read-missing-column' @('read', "$F/missing-column")

# ---------------------------------------------------------------- 分区 / 统计
Invoke-Case '90-scan-partition-table-all' @('scan', "$F/partition-table")
Invoke-Case '91-scan-partition-table-dt' @('scan', "$F/partition-table", '--partition', 'dt=2026-01-01')
Invoke-Case '92-read-partition-table' @('read', "$F/partition-table")
Invoke-Case '93-read-partition-null-value' @('read', "$F/partition-null-value")
Invoke-Case '94-scan-partition-null-value' @('scan', "$F/partition-null-value", '--partition', 'dt=null')
Invoke-Case '95-scan-stats-string-where' @('scan', "$F/stats-string-bool", '--where', 'name>=b')
Invoke-Case '96-scan-stats-bool-where' @('scan', "$F/stats-string-bool", '--where', 'ok>=1')
Invoke-Case '97-scan-stats-numeric-where' @('scan', "$F/stats-string-bool", '--where', 'id>=10')
Invoke-Case '98-scan-min-max-eq-gt' @('scan', "$F/stats-min-max-eq", '--where', 'id>5')
Invoke-Case '99-scan-min-max-eq-ge' @('scan', "$F/stats-min-max-eq", '--where', 'id>=5')
Invoke-Case '100-inspect-stats-object' @('inspect', "$F/stats-object")
Invoke-Case '101-scan-stats-object-where' @('scan', "$F/stats-object", '--where', 'id>=100')
Invoke-Case '102-read-stats-object' @('read', "$F/stats-object")

# ---------------------------------------------------------------- CLI create
$schemaFlat = '{"type":"struct","fields":[{"name":"id","type":"long","nullable":true,"metadata":{}},{"name":"name","type":"string","nullable":true,"metadata":{}}]}'
$schemaBad = '{not a schema'
$schemaNested = '{"type":"struct","fields":[{"name":"id","type":"long","nullable":true,"metadata":{}},{"name":"tags","type":{"type":"array","elementType":"string","containsNull":true},"nullable":true,"metadata":{}}]}'
$schemaReduced = '{"type":"struct","fields":[{"name":"id","type":"long","nullable":true,"metadata":{}}]}'

Invoke-Case '110-create-flat' @('create', "$F/cli-created", $schemaFlat)
Invoke-Case '111-inspect-created-empty' @('inspect', "$F/cli-created")
Invoke-Case '112-read-created-empty' @('read', "$F/cli-created")
Invoke-Case '113-create-invalid-schema' @('create', "$F/cli-created-bad", $schemaBad)
Invoke-Case '114-inspect-created-bad' @('inspect', "$F/cli-created-bad")
Invoke-Case '115-create-nested-schema' @('create', "$F/cli-nested", $schemaNested)
Invoke-Case '116-inspect-created-nested' @('inspect', "$F/cli-nested")
Invoke-Case '117-read-created-nested' @('read', "$F/cli-nested")
Invoke-Case '118-create-partition-table' @('create', "$F/cli-partition", $schemaFlat, '--partition', 'dt')
Invoke-Case '119-inspect-created-partition' @('inspect', "$F/cli-partition")

# ---------------------------------------------------------------- 示例程序（写入 2 文件 + NULL）
Invoke-External '120-moon-run-interop-writer' 'moon' @('run', '--target', 'native', 'src/examples/interop_flat_writer', '--', "$F/appended-flat") 'moon run --target native src/examples/interop_flat_writer -- artifacts/protocol-audit/fixtures/appended-flat'
Invoke-Case '121-inspect-appended-flat' @('inspect', "$F/appended-flat")
Invoke-Case '122-read-appended-flat' @('read', "$F/appended-flat")
Invoke-Case '123-read-appended-flat-limit2' @('read', "$F/appended-flat", '--limit', '2')

# 在已有数据表上 create_table 覆盖 v0：schema 变更是否被发现？
if (Test-Path "$F/appended-flat") {
  Copy-Item -Recurse -Force "$F/appended-flat" "$F/cli-overwrite"
}
Invoke-Case '124-create-overwrite-existing' @('create', "$F/cli-overwrite", $schemaReduced)
Invoke-Case '125-inspect-overwritten' @('inspect', "$F/cli-overwrite")
Invoke-Case '126-read-overwritten' @('read', "$F/cli-overwrite")

Invoke-External '127-moon-run-read-covid-example' 'moon' @('run', '--target', 'native', 'src/examples/read_covid') 'moon run --target native src/examples/read_covid'

Set-Content -Path (Join-Path $PSScriptRoot 'commands.txt') -Encoding utf8 -Value ($commands -join "`n")
Remove-Item -Recurse -Force $tmpDir -ErrorAction SilentlyContinue
Write-Host ''
Write-Host "done: $($commands.Count) lines in commands.txt"
