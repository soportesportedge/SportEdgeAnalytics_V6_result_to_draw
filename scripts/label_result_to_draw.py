"""Append only result_to_draw, retaining original CSV record bytes and order.

The sole target is defined in Variables!C223:K223 of the supplied dictionary.
An eligible snapshot has abs(dif_score) == 1. It is positive only when a
subsequent observed equalization survives later same-team score reversals and
the permitted closing record is tied. Missing closure is unresolved for an
eligible snapshot. A closure is FT; without FT, the last record is permitted
only when that exact (id, market_id) group contains a minute >= 90.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

from inspect_source import DICTIONARY, ROOT, SOURCE


def load_source():
    raw = SOURCE.read_bytes()
    text = raw.decode("utf-8-sig")
    reader = csv.reader(io.StringIO(text, newline=""))
    records = list(reader)
    headers, values = records[0], records[1:]
    assert len(headers) == 221 and len(headers) == len(set(headers))
    assert "result_to_draw" not in headers
    assert all(len(row) == len(headers) for row in values)
    # These sources contain one physical line per CSV record. Prove it before
    # direct byte appending, rather than reserializing any original field.
    physical = raw.splitlines(keepends=True)
    assert len(physical) == len(records), "Multiline/blank CSV record: byte append requires adaptation"
    assert all(line.rstrip(b"\r\n") for line in physical)
    return raw, headers, [dict(zip(headers, value)) for value in values], physical


def group_items(rows):
    groups = defaultdict(list)
    for original_index, row in enumerate(rows):
        groups[(row["id"], row["market_id"])].append((original_index, row))
    return {key: sorted(items, key=lambda item: (
        datetime.fromisoformat(item[1]["captured_at"]), int(item[1]["snapshot_minute"]), item[0]))
        for key, items in groups.items()}


def find_closure(items):
    ft = [position for position, (_, row) in enumerate(items)
          if row["state_developer_name"] == "FT"]
    if ft:
        return ft[0], "FT"
    if any(int(row["snapshot_minute"]) >= 90 for _, row in items):
        return len(items) - 1, "ultimo_registro_grupo_con_minuto_ge_90"
    return None, "sin_cierre"


def surviving_equalizations(items):
    # Individual goal increments are stacked independently for home and away.
    # Later decreases cancel the most recent increments of the same team.
    events, stacks, cancellations, candidates = [], [[], []], [], []
    score_fields = ("home_score", "away_score")
    for position in range(1, len(items)):
        prior = items[position - 1][1]
        row = items[position][1]
        deltas = [int(row[field]) - int(prior[field]) for field in score_fields]
        event_ids_here = [[], []]
        for team in (0, 1):
            delta = deltas[team]
            if delta > 0:
                for _ in range(delta):
                    event_id = len(events)
                    events.append({"position": position, "team": team, "active": True})
                    stacks[team].append(event_id)
                    event_ids_here[team].append(event_id)
            elif delta < 0:
                reversed_ids = []
                for _ in range(-delta):
                    if stacks[team]:
                        event_id = stacks[team].pop()
                        events[event_id]["active"] = False
                        reversed_ids.append(event_id)
                cancellations.append({"source_csv_row": items[position][0] + 2,
                    "team": "home" if team == 0 else "away", "decrease": -delta,
                    "reversed_increment_csv_rows": [items[events[event_id]["position"]][0] + 2 for event_id in reversed_ids],
                    "decrease_against_preexisting_score": -delta - len(reversed_ids)})
        before, after = int(prior["dif_score"]), int(row["dif_score"])
        if abs(before) == 1 and after == 0:
            trailing_team = 0 if before == -1 else 1
            if deltas[trailing_team] > 0 and deltas[1 - trailing_team] == 0:
                # Require a scoring equalizer, not a correction-only move to 0.
                candidates.append((position, event_ids_here[trailing_team][-1]))
    surviving = [position for position, event_id in candidates if events[event_id]["active"]]
    revoked = [position for position, event_id in candidates if not events[event_id]["active"]]
    return surviving, revoked, cancellations


def label_group(items, pending_policy):
    closure_position, closure_kind = find_closure(items)
    horizon = items if closure_position is None else items[:closure_position + 1]
    equalizations, revoked, cancellations = surviving_equalizations(horizon)
    labels, reasons = [], []
    for position, (_, row) in enumerate(items):
        if abs(int(row["dif_score"])) != 1:
            label, reason = "false", "diferencia_actual_no_es_mas_menos_1"
        elif closure_position is not None and position >= closure_position:
            label, reason = "false", "sin_trayectoria_futura_antes_del_cierre"
        elif closure_position is None:
            label = "" if pending_policy == "null" else "false"
            reason = "pendiente_sin_cierre" if pending_policy == "null" else "false_sin_cierre_por_criterio_usuario"
        elif int(items[closure_position][1]["dif_score"]) != 0:
            label, reason = "false", "cierre_no_empatado"
        elif any(event_position > position for event_position in equalizations):
            label, reason = "true", "igualacion_futura_no_revertida_y_cierre_empatado"
        else:
            label, reason = "false", "sin_igualacion_futura_observable_no_revertida"
        labels.append(label)
        reasons.append(reason)
    return labels, reasons, {"closure_kind": closure_kind,
        "closure_source_csv_row": None if closure_position is None else items[closure_position][0] + 2,
        "closure_minute": None if closure_position is None else items[closure_position][1]["snapshot_minute"],
        "closure_score": None if closure_position is None else [items[closure_position][1]["home_score"], items[closure_position][1]["away_score"]],
        "surviving_equalization_csv_rows": [items[position][0] + 2 for position in equalizations],
        "revoked_equalization_csv_rows": [items[position][0] + 2 for position in revoked],
        "possible_var_or_score_corrections": cancellations}


def independent_labels(items, pending_policy):
    # Independent check: suffix minima replace event stacks. A single equalizer
    # is valid only if its scorer's score never drops below that new score.
    ft_positions = [i for i, (_, row) in enumerate(items) if row["state_developer_name"] == "FT"]
    close = ft_positions[0] if ft_positions else (len(items) - 1 if max(int(row["snapshot_minute"]) for _, row in items) >= 90 else None)
    stop = len(items) if close is None else close + 1
    ties = []
    for j in range(1, stop):
        left, right = items[j - 1][1], items[j][1]
        d = int(left["dif_score"])
        if abs(d) != 1 or int(right["dif_score"]) != 0:
            continue
        field = "home_score" if d == -1 else "away_score"
        other = "away_score" if d == -1 else "home_score"
        if int(right[field]) > int(left[field]) and right[other] == left[other]:
            if min(int(items[k][1][field]) for k in range(j, stop)) >= int(right[field]):
                ties.append(j)
    output = []
    for i, (_, row) in enumerate(items):
        if abs(int(row["dif_score"])) != 1:
            output.append("false")
        elif close is None:
            output.append("" if pending_policy == "null" else "false")
        else:
            positive = i < close and int(items[close][1]["dif_score"]) == 0 and any(j > i for j in ties)
            output.append("true" if positive else "false")
    return output


def append_field(line, value):
    body = line.rstrip(b"\r\n")
    ending = line[len(body):]
    return body + b"," + value.encode("ascii") + ending


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--minute-policy", choices=("actual", "group70"), default="actual")
    parser.add_argument("--pending-policy", choices=("null", "false"), default="null")
    args = parser.parse_args()
    raw, headers, rows, physical = load_source()
    assert all(int(row["snapshot_minute"]) >= 70 for row in rows), "Source contains rows outside the requested minute >=70 population"
    assert not any(any(row[key] == "" for key in ("home_score", "away_score", "dif_score")) for row in rows)
    assert not any(int(row["dif_score"]) != int(row["home_score"]) - int(row["away_score"]) for row in rows)
    groups = group_items(rows)
    labels = [None] * len(rows)
    reason_counts = Counter()
    details = []
    for key, items in groups.items():
        local_labels, reasons, detail = label_group(items, args.pending_policy)
        assert local_labels == independent_labels(items, args.pending_policy), f"Independent mismatch for {key}"
        if args.minute_policy == "group70":
            starts = [i for i, (_, row) in enumerate(items) if row["snapshot_minute"] == "70"]
            if not starts:
                chosen = "" if args.pending_policy == "null" else "false"
                reason = "sin_snapshot_minuto_70"
            else:
                chosen, reason = local_labels[starts[0]], reasons[starts[0]]
            local_labels, reasons = [chosen] * len(items), [reason] * len(items)
        for (original_index, _), label, reason in zip(items, local_labels, reasons):
            labels[original_index] = label
            reason_counts[reason] += 1
        details.append({"id": key[0], "market_id": key[1], "rows": len(items),
            "label_counts": dict(Counter(label or "NULL" for label in local_labels)), **detail})
    assert all(label in ("true", "false", "") for label in labels)
    output_bytes = b"".join([append_field(physical[0], "result_to_draw")] +
                            [append_field(line, label) for line, label in zip(physical[1:], labels)])
    output_folder = ROOT / "outputs" / "etiquetado_result_to_draw_20261011"
    output_folder.mkdir(parents=True, exist_ok=True)
    output_path = output_folder / "Match_centre_base_modelos_V2_etiquetada_result_to_draw.csv"
    output_path.write_bytes(output_bytes)

    # Verify the exported data both textually and as CSV fields. Removing only
    # the appended field must reconstruct the entire original byte stream.
    saved = output_path.read_bytes()
    reconstructed = b"".join(line.rsplit(b",", 1)[0] + line[len(line.rstrip(b"\r\n")):]
                             for line in saved.splitlines(keepends=True))
    assert reconstructed == raw, "Original CSV bytes/order changed"
    exported = list(csv.reader(io.StringIO(saved.decode("utf-8-sig"), newline="")))
    original = list(csv.reader(io.StringIO(raw.decode("utf-8-sig"), newline="")))
    assert exported[0] == headers + ["result_to_draw"]
    assert len(exported) == len(original)
    assert all(target[:-1] == source for source, target in zip(original, exported))
    assert [row[-1] for row in exported[1:]] == labels
    assert SOURCE.read_bytes() == raw
    dictionary_hash = hashlib.sha256(DICTIONARY.read_bytes()).hexdigest()
    source_inspection = json.loads((ROOT / "audit" / "source_inspection.json").read_text(encoding="utf-8"))
    assert dictionary_hash == source_inspection["dictionary_sha256"]
    assert hashlib.sha256(raw).hexdigest() == source_inspection["source_sha256"]
    label_counts = dict(Counter(label or "NULL" for label in labels))
    audit = {"output_path": str(output_path), "source_path": str(SOURCE), "dictionary_path": str(DICTIONARY),
        "source_sha256": hashlib.sha256(raw).hexdigest(), "dictionary_sha256": dictionary_hash,
        "output_sha256": hashlib.sha256(saved).hexdigest(), "source_rows": len(rows), "output_rows": len(rows),
        "source_columns": len(headers), "output_columns": len(headers) + 1,
        "column_added": "result_to_draw", "minute_policy": args.minute_policy, "minimum_minute": 70, "pending_policy": args.pending_policy,
        "label_counts": label_counts, "reason_counts": dict(reason_counts),
        "verification": {"source_values_exact": True, "source_bytes_reconstructed_exact": True,
            "source_order_preserved": True, "source_files_unchanged": True,
            "independent_label_mismatches": 0, "groups": len(groups), "rows_checked": len(rows)},
        "group_details": details}
    audit_path = ROOT / "audit" / "labeling_validation.json"
    audit_path.write_text(json.dumps(audit, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({key: audit[key] for key in ("output_path", "source_rows", "source_columns", "output_columns",
        "minute_policy", "pending_policy", "label_counts", "reason_counts", "verification")}, ensure_ascii=True, indent=2))


if __name__ == "__main__":
    main()
