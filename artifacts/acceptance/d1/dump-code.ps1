$ErrorActionPreference='Continue'
$out = "artifacts/acceptance/d1/code-evidence.out"
"### grep tokens in src/ (repo)" | Set-Content -Encoding utf8 $out
foreach ($tok in @('deletionVector','readerFeatures','writerFeatures','columnMapping','is_writable_type','Repeated columns are not supported yet','total_rows')) {
  $hits = Get-ChildItem src -Recurse -File -Include *.mbt,*.pkg | Select-String -SimpleMatch $tok
  "----- token: $tok  hits=$($hits.Count)" | Add-Content -Encoding utf8 $out
  foreach ($h in $hits) { $rel = $h.Path.Replace((Get-Location).Path + '\',''); "  ${rel}:$($h.LineNumber): $($h.Line.Trim())" | Add-Content -Encoding utf8 $out }
}
"`n### key code lines" | Add-Content -Encoding utf8 $out
function DumpLines([string]$file,[int]$start,[int]$end) {
  "----- $file : $start-$end" | Add-Content -Encoding utf8 $out
  $lines = Get-Content $file
  for ($i=$start; $i -le $end -and $i -le $lines.Count; $i++) { "  ${i}: $($lines[$i-1])" | Add-Content -Encoding utf8 $out }
}
DumpLines 'src/actions.mbt' 14 26
DumpLines 'src/actions.mbt' 86 93
DumpLines 'src/actions.mbt' 144 150
DumpLines 'src/snapshot.mbt' 57 71
DumpLines 'src/snapshot.mbt' 105 118
DumpLines 'src/snapshot.mbt' 144 150
DumpLines 'src/write.mbt' 163 186
DumpLines 'src/write.mbt' 195 240
DumpLines 'src/cmd/delta/main.mbt' 42 48
DumpLines 'src/cmd/delta/main.mbt' 142 172
DumpLines 'src/cmd/delta/main.mbt' 200 206
DumpLines 'src/cmd/delta/main.mbt' 245 252
DumpLines 'src/schema.mbt' 110 132
DumpLines 'src/log.mbt' 99 130
DumpLines 'src/read.mbt' 140 152
DumpLines 'src/types.mbt' 128 146
