# -*- coding: utf-8 -*-
"""D5：DuckDB 独立读 D3 nested fixture（第二 oracle 复核）。"""
import duckdb

con = duckdb.connect()
try:
    con.execute("LOAD delta")
except Exception:
    con.execute("INSTALL delta")
    con.execute("LOAD delta")
rows = con.execute("SELECT * FROM delta_scan('artifacts/interop-nested/engine-written')").fetchall()
print("row_count=", len(rows))
for r in rows:
    print(r)
print("schema=", con.execute("DESCRIBE SELECT * FROM delta_scan('artifacts/interop-nested/engine-written')").fetchall())
ext = con.execute("SELECT extension_version FROM duckdb_extensions() WHERE extension_name='delta'").fetchall()
print("delta_extension=", ext)
