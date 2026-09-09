"""CSV 导出（UTF-8 BOM，Excel 打开不乱码）。"""
from __future__ import annotations

import csv
from typing import Dict, Iterable, List, Sequence


def rows_to_csv(path: str, headers: Sequence[str], rows: Iterable[Dict]) -> str:
    """把字典行导出为 CSV，返回路径。行中 None 写为空串。"""
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f)
        writer.writerow(list(headers))
        for row in rows:
            writer.writerow([_cell(row.get(h)) for h in headers])
    return path


def _cell(v):
    if v is None:
        return ""
    if isinstance(v, float):
        return round(v, 4)
    return v
