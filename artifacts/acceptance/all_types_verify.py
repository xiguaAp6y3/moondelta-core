"""Verify moondelta all-primitive table with delta-rs and DuckDB."""
import json
from pathlib import Path

import duckdb
from deltalake import DeltaTable

ROOT = Path(__file__).parents[2] / "artifacts/interop-flat/all-primitives-written"
EXPECTED = [
    ("b", "byte"), ("s", "short"), ("i", "integer"), ("l", "long"),
    ("f", "float"), ("d", "double"), ("bo", "boolean"), ("st", "string"),
    ("bin", "binary"), ("dt", "date"), ("ts", "timestamp"),
]
EXPECTED_ROW = {
    "b": 7, "s": 300, "i": 42, "l": 123456789, "f": 1.5, "d": 2.25,
    "bo": True, "st": "hello", "bin": b"raw",
}
EXPECTED_NULL_ROW = {name: None for name in EXPECTED_ROW} | {"dt": None, "ts": None}

dt = DeltaTable(str(ROOT))
schema = [(f["name"], f["type"]) for f in json.loads(dt.schema().to_json())["fields"]]
assert schema == EXPECTED, schema
rows = dt.to_pyarrow_table().to_pylist()
assert len(rows) == 2
for key, value in EXPECTED_ROW.items():
    assert rows[0][key] == value, (key, rows[0][key], value)
assert str(rows[0]["dt"]) == "2026-01-01"
assert rows[0]["ts"].isoformat() == "2020-01-01T00:00:00+00:00"
assert all(value is None for value in rows[1].values()), rows[1]

con = duckdb.connect()
con.execute("SET TimeZone='UTC'")
con.execute("INSTALL delta; LOAD delta;")
# DuckDB 的 TIMESTAMPTZ 读取需要 pytz；该环境未安装，因此 timestamp 列由
# delta-rs/pyarrow 验证，其余 10 列由 DuckDB 独立验证。
duck_rows = con.execute(
    "SELECT b,s,i,l,f,d,bo,st,bin,dt FROM delta_scan(?) ORDER BY i NULLS LAST",
    [str(ROOT)],
).fetchall()
assert len(duck_rows) == 2
assert tuple(duck_rows[0][:9]) == tuple(EXPECTED_ROW.values())
assert str(duck_rows[0][9]) == "2026-01-01"
assert all(value is None for value in duck_rows[1])

print("PASS delta-rs schema: 11 primitive types")
print("PASS delta-rs values: first row exact, second row all null")
print("PASS DuckDB delta_scan: 10 non-timestamp columns exact")
print("NOTE DuckDB timestamp check skipped: environment lacks pytz")
