"""CSV output, written with a BOM so Excel opens UTF-8 correctly."""

from __future__ import annotations

import csv
from pathlib import Path

from orynx.export.builder import COLUMNS, LeadRow


def write_csv(rows: list[LeadRow], path: Path) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=COLUMNS, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow(row.as_dict())
    return path
