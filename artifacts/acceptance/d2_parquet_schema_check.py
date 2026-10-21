# -*- coding: utf-8 -*-
"""D5：D2 R-1 物理 nullability 独立核对。"""
import glob
import pyarrow.parquet as pq

for label, pat in (("engine-written", "artifacts/interop-flat/engine-written/*.parquet"),
                   ("moondelta-written", "artifacts/interop-flat/moondelta-written/*.parquet")):
    for p in sorted(glob.glob(pat)):
        sch = pq.read_schema(p)
        print(label, p.split("/")[-1], [(f.name, str(f.type), f.nullable) for f in sch])
