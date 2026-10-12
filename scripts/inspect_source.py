"""Read-only inspection of the supplied dictionary and source CSV."""
from __future__ import annotations

import csv
import hashlib
import json
import warnings
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

import openpyxl

ROOT = Path(__file__).resolve().parents[1]
SOURCE = Path(r"C:\Users\SportEdge\Downloads\Match_centre_base_modelos_V2 - Match_centre_base_modelos_V2.csv")
DICTIONARY = Path(r"C:\Users\SportEdge\Downloads\Descripcion_variables_actualizada_V2_analitico.xlsm")


def inspect():
    warnings.filterwarnings("ignore", message="Unknown extension is not supported")
    book = openpyxl.load_workbook(DICTIONARY, read_only=True, data_only=True)
    sheet_rows = list(book["Variables"].iter_rows(values_only=True))
    dictionary = []
    for row_number, row in enumerate(sheet_rows[1:], 2):
        if len(row) >= 4 and row[2]:
            dictionary.append({"excel_row": row_number, **dict(zip(sheet_rows[0], row))})
    book.close()
    with SOURCE.open(encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream)
        headers = reader.fieldnames
        rows = list(reader)
    groups = defaultdict(list)
    for row_index, row in enumerate(rows):
        groups[(row["id"], row["market_id"])].append((row_index, row))
    ordered_groups = {}
    for key, items in groups.items():
        ordered_groups[key] = sorted(items, key=lambda item: (
            datetime.fromisoformat(item[1]["captured_at"]),
            int(item[1]["snapshot_minute"]), item[0]))
    incomplete, decreases, simultaneous, ties, order_changes = [], [], [], [], []
    complete_closing_diff = Counter()
    for key, items in ordered_groups.items():
        if items != groups[key]:
            order_changes.append(key)
        closed = any(int(row["snapshot_minute"]) >= 90 or row["state_developer_name"] == "FT" for _, row in items)
        if not closed:
            incomplete.append({"id": key[0], "market_id": key[1], "rows": len(items),
                               "last_minute": items[-1][1]["snapshot_minute"],
                               "eligible_rows_abs_diff_1": sum(abs(int(row["dif_score"])) == 1 for _, row in items)})
        else:
            complete_closing_diff[items[-1][1]["dif_score"]] += 1
        for (left_index, left), (right_index, right) in zip(items, items[1:]):
            dh = int(right["home_score"]) - int(left["home_score"])
            da = int(right["away_score"]) - int(left["away_score"])
            detail = {"id": key[0], "market_id": key[1], "source_csv_row": right_index + 2,
                      "from_score": [int(left["home_score"]), int(left["away_score"])],
                      "to_score": [int(right["home_score"]), int(right["away_score"])],
                      "captured_at": right["captured_at"]}
            if dh < 0 or da < 0:
                decreases.append(detail)
            if dh > 0 and da > 0:
                simultaneous.append(detail)
            if left["captured_at"] == right["captured_at"] and left["snapshot_minute"] == right["snapshot_minute"]:
                ties.append(detail)
    dictionary_names = [item["VARIABLE"] for item in dictionary]
    source_bytes = SOURCE.read_bytes()
    result = {
        "source": str(SOURCE), "dictionary": str(DICTIONARY),
        "source_sha256": hashlib.sha256(source_bytes).hexdigest(),
        "dictionary_sha256": hashlib.sha256(DICTIONARY.read_bytes()).hexdigest(),
        "rows": len(rows), "columns": len(headers), "headers": headers,
        "unique_fixtures": len({row["id"] for row in rows}), "groups_id_market_id": len(groups),
        "dictionary_variables": len(dictionary),
        "dictionary_targets": [item for item in dictionary if item.get("TIPO") == "TARGET"],
        "csv_fields_missing_in_dictionary": sorted(set(headers) - set(dictionary_names)),
        "dictionary_fields_missing_in_csv": sorted(set(dictionary_names) - set(headers)),
        "dictionary_predictors": sum(str(item.get("PREDICTORA")).lower() in ("si", "sí") for item in dictionary),
        "minute_counts": dict(Counter(row["snapshot_minute"] for row in rows)),
        "market_counts": dict(Counter(row["market_id"] for row in rows)),
        "state_counts": dict(Counter(row["state_developer_name"] for row in rows)),
        "cluster_counts": dict(Counter(row["cluster"] for row in rows)),
        "captured_at_min": min(row["captured_at"] for row in rows),
        "captured_at_max": max(row["captured_at"] for row in rows),
        "missing_scores": sum(any(row[key] == "" for key in ("home_score", "away_score", "dif_score")) for row in rows),
        "difference_inconsistencies": sum(int(row["home_score"]) - int(row["away_score"]) != int(row["dif_score"]) for row in rows),
        "complete_groups": len(groups) - len(incomplete), "incomplete_groups": incomplete,
        "incomplete_rows": sum(item["rows"] for item in incomplete),
        "complete_closing_difference_counts": dict(complete_closing_diff),
        "score_decreases": decreases, "simultaneous_score_increases": simultaneous,
        "chronology_ties": ties, "groups_requiring_internal_sort": order_changes,
        "null_counts": {header: sum(row[header] == "" for row in rows) for header in headers},
        "physical_lines": len(source_bytes.splitlines()),
        "source_bytes": len(source_bytes),
    }
    folder = ROOT / "audit"
    folder.mkdir(exist_ok=True)
    (folder / "source_inspection.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    concise = {key: result[key] for key in ("rows", "columns", "unique_fixtures", "groups_id_market_id",
        "dictionary_variables", "dictionary_predictors", "csv_fields_missing_in_dictionary", "dictionary_fields_missing_in_csv",
        "complete_groups", "incomplete_rows", "complete_closing_difference_counts", "physical_lines")}
    concise.update({"incomplete_groups": len(incomplete), "score_decreases": len(decreases),
        "simultaneous_increases": len(simultaneous), "chronology_ties": len(ties),
        "groups_requiring_internal_sort": len(order_changes)})
    print(json.dumps(concise, ensure_ascii=True, indent=2))


if __name__ == "__main__":
    inspect()
