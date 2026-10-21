import glob
import sys

import pyarrow.parquet as pq


def dump(path: str) -> None:
    schema = pq.read_schema(path)
    md = pq.ParquetFile(path).metadata
    print(f"{path}")
    print(f"  num_rows={md.num_rows} num_columns={md.num_columns}")
    for field in schema:
        print(f"  column {field.name}: physical/logical type={field.type} nullable={field.nullable}")
    print()


def main() -> None:
    for pattern in sys.argv[1:]:
        matches = sorted(glob.glob(pattern))
        if not matches:
            print(f"{pattern}: no match")
        for path in matches:
            dump(path)


if __name__ == "__main__":
    main()
