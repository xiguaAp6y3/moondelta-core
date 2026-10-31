"""Regenerate small negative checkpoint fixtures with the development PyArrow environment."""
from pathlib import Path
import shutil

import pyarrow as pa
import pyarrow.parquet as pq

ROOT = Path(__file__).parent / "checkpoint-negative"
SOURCE = Path("artifacts/protocol-audit/fixtures/checkpoint-only/_delta_log/00000000000000000010.checkpoint.parquet")
table = pq.read_table(SOURCE)
rows = table.to_pylist()


def write_case(name, actions):
    out = ROOT / name / "_delta_log"
    if out.parent.exists():
        shutil.rmtree(out.parent)
    out.mkdir(parents=True)
    pq.write_table(pa.Table.from_pylist(actions, schema=table.schema), out / "00000000000000000010.checkpoint.parquet")
    shutil.copy2(SOURCE.parent / "_last_checkpoint", out / "_last_checkpoint")


add = next(row["add"] for row in rows if row["add"] is not None).copy()
add["deletionVector"] = {
    "storageType": "u", "pathOrInlineDv": "dv.bin", "offset": 1,
    "sizeInBytes": 1, "cardinality": 1, "maxRowIndex": 0,
}
write_case("deletion-vector", [next(row for row in rows if row["protocol"] is not None), next(row for row in rows if row["metaData"] is not None), {**{key: None for key in table.column_names}, "add": add}])
unknown_schema = pa.schema(list(table.schema) + [pa.field("futureAction", pa.struct([("value", pa.string())]))])
unknown_out = ROOT / "unknown-action" / "_delta_log"
if unknown_out.parent.exists():
    shutil.rmtree(unknown_out.parent)
unknown_out.mkdir(parents=True)
unknown_rows = [
    {**{key: None for key in table.column_names}, "futureAction": {"value": "reject"}},
    next(row for row in rows if row["protocol"] is not None),
    next(row for row in rows if row["metaData"] is not None),
]
pq.write_table(pa.Table.from_pylist(unknown_rows, schema=unknown_schema), unknown_out / "00000000000000000010.checkpoint.parquet")
shutil.copy2(SOURCE.parent / "_last_checkpoint", unknown_out / "_last_checkpoint")
write_case("missing-protocol", table.select([name for name in table.column_names if name != "protocol"]).to_pylist())
write_case("missing-metadata", table.select([name for name in table.column_names if name != "metaData"]).to_pylist())
print("Generated four negative checkpoint fixtures under", ROOT)
