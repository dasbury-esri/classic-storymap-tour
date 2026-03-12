#!/usr/bin/env python
"""Step 2: Rewire dependent apps using mapping report from rebuild step."""

from __future__ import annotations

import argparse
import csv
import json
import warnings
from pathlib import Path
from typing import Any, Dict, List

from migrate_legacy_webmaps import (
    InsecureRequestWarning,
    _filtered_showwarning,
    connect_gis,
    find_dependent_apps,
    output_path,
    replace_ids_in_object,
    update_app_url,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Step 2 dependent app rewiring.")
    parser.add_argument("--auth-mode", choices=["keyring", "profile", "home"], default="keyring")
    parser.add_argument("--username", default=None)
    parser.add_argument("--password", default=None)
    parser.add_argument("--profile", default=None)
    parser.add_argument("--mapping-report", required=True, help="CSV produced by rebuild_legacy_webmaps.py")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--app-scan-mode", choices=["targeted", "full"], default="targeted")
    parser.add_argument("--max-app-items", type=int, default=5000)
    parser.add_argument("--heartbeat-every", type=int, default=100)
    parser.add_argument("--report-csv", default="legacy_webmap_rewire_report.csv")
    return parser.parse_args()


def load_mapping_rows(path: Path) -> List[Dict[str, Any]]:
    with path.open("r", newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        rows = list(reader)

    out = []
    for row in rows:
        status = (row.get("status") or "").strip()
        if status not in {"OK", "DRY_RUN"}:
            continue
        source_item_id = (row.get("source_item_id") or "").strip()
        id_map_json = row.get("id_map_json") or "{}"
        if not source_item_id:
            continue
        try:
            id_map = json.loads(id_map_json)
            if not isinstance(id_map, dict):
                id_map = {}
        except Exception:
            id_map = {}
        out.append({
            "source_item_id": source_item_id,
            "new_item_id": (row.get("new_item_id") or "").strip(),
            "id_map": id_map,
        })
    return out


def write_report(path: Path, rows: List[Dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=[
                "source_item_id",
                "new_item_id",
                "dependent_app_id",
                "dependent_app_title",
                "status",
                "error",
            ],
        )
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def main() -> None:
    warnings.filterwarnings("ignore", category=InsecureRequestWarning)
    warnings.showwarning = _filtered_showwarning

    args = parse_args()
    mapping_path = Path(args.mapping_report)
    if not mapping_path.is_absolute():
        mapping_path = output_path(args.mapping_report)
    if not mapping_path.exists():
        raise RuntimeError(f"Mapping report not found: {mapping_path}")

    mappings = load_mapping_rows(mapping_path)
    if not mappings:
        raise RuntimeError("No successful mapping rows found in report")

    gis = connect_gis(args)
    report_rows: List[Dict[str, Any]] = []

    for mapping in mappings:
        source_id = mapping["source_item_id"]
        id_map = mapping["id_map"]
        if source_id not in id_map and mapping.get("new_item_id"):
            id_map[source_id] = mapping["new_item_id"]

        dependents = find_dependent_apps(
            gis,
            source_id,
            max_items=args.max_app_items,
            scan_mode=args.app_scan_mode,
            heartbeat_every=args.heartbeat_every,
        )
        print(f"{source_id}: dependent apps found={len(dependents)}")

        for app in dependents:
            row = {
                "source_item_id": source_id,
                "new_item_id": id_map.get(source_id, ""),
                "dependent_app_id": app.itemid,
                "dependent_app_title": app.title,
                "status": "NO_CHANGE",
                "error": "",
            }
            try:
                app_data = app.get_data(try_json=True)
                changed = False
                new_data = app_data

                if app_data is not None:
                    replaced = replace_ids_in_object(app_data, id_map)
                    if json.dumps(replaced, sort_keys=True) != json.dumps(app_data, sort_keys=True):
                        new_data = replaced
                        changed = True

                new_url = update_app_url(app.url or "", id_map)
                if new_url != (app.url or ""):
                    changed = True

                if changed:
                    if args.dry_run:
                        row["status"] = "DRY_RUN_UPDATED"
                    else:
                        props = {"url": new_url}
                        if new_data is not None:
                            props["text"] = json.dumps(new_data)
                        app.update(item_properties=props)
                        row["status"] = "UPDATED"
            except Exception as ex:
                row["status"] = "FAILED"
                row["error"] = str(ex)

            report_rows.append(row)

    report_path = output_path(args.report_csv)
    write_report(report_path, report_rows)
    print(f"Wrote app rewire report: {report_path}")
    updated = sum(1 for r in report_rows if r["status"] in {"UPDATED", "DRY_RUN_UPDATED"})
    failed = sum(1 for r in report_rows if r["status"] == "FAILED")
    print(f"Completed app rewiring: updated={updated}, failed={failed}, total={len(report_rows)}")


if __name__ == "__main__":
    main()
