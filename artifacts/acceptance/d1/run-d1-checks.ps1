$ErrorActionPreference = 'Continue'
$delta = "_build\native\debug\build\cmd\delta\delta.exe"
New-Item -ItemType Directory -Force artifacts/acceptance/d1 | Out-Null
function Run-Case([string]$name, [string]$exe, [string[]]$argv) {
  $cmdline = "$exe " + ($argv -join ' ')
  $outfile = "artifacts/acceptance/d1/$name.out"
  $combined = & $exe @argv 2>&1 | Out-String
  $code = $LASTEXITCODE
  "### $cmdline" | Set-Content -Encoding utf8 $outfile
  "EXIT=$code" | Add-Content -Encoding utf8 $outfile
  $combined | Add-Content -Encoding utf8 $outfile
  Write-Output ("=== $name EXIT=$code")
  Write-Output ($combined.TrimEnd())
}
$a = "artifacts/protocol-audit/fixtures"
Run-Case "01-version" $delta @("version")
Run-Case "02-reader-v2-inspect" $delta @("inspect","$a/protocol-reader-v2")
Run-Case "03-reader-v2-read" $delta @("read","$a/protocol-reader-v2")
Run-Case "04-reader-v3-read" $delta @("read","$a/protocol-reader-v3-features")
Run-Case "05-deletion-vector-read" $delta @("read","$a/deletion-vector-add")
Run-Case "06-checkpoint-truncated-inspect" $delta @("inspect","$a/checkpoint-truncated")
Run-Case "07-checkpoint-only-inspect" $delta @("inspect","$a/checkpoint-only")
Run-Case "08-nested-struct-read" $delta @("read","$a/nested-struct")
Run-Case "09-nested-list-read" $delta @("read","$a/nested-list")
Run-Case "10-nested-map-read" $delta @("read","$a/nested-map")
Run-Case "11-codec-zstd-read" $delta @("read","$a/codec-zstd")
Run-Case "12-type-mismatch-read" $delta @("read","$a/type-mismatch")
Run-Case "13-stats-object-inspect" $delta @("inspect","$a/stats-object")
Run-Case "14-stats-object-scan" $delta @("scan","$a/stats-object","--where","id>=100")
Run-Case "15-fake-partition-scan" $delta @("scan","fixtures/delta/table_a","--partition","id=7")
Run-Case "16-nonexistent-inspect" $delta @("inspect","artifacts/acceptance/d1/does-not-exist")
Run-Case "17-no-protocol-inspect" $delta @("inspect","$a/no-protocol")
Run-Case "18-empty-log-inspect" $delta @("inspect","$a/empty-log")
Run-Case "19-missing-data-file-read" $delta @("read","$a/missing-data-file")
Run-Case "20-nonstandard-log-name" $delta @("inspect","$a/nonstandard-log-name")
Run-Case "21-date-where-scan" $delta @("scan","fixtures/delta/covid","--where","date>=2020-05-01")
Run-Case "22-table-a-inspect" $delta @("inspect","fixtures/delta/table_a")
Run-Case "23-table-a-scan-prune" $delta @("scan","fixtures/delta/table_a","--where","id>=60")
Run-Case "24-covid-read-limit5" $delta @("read","fixtures/delta/covid","--limit","5")
