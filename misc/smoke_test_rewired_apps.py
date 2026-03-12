#!/usr/bin/env python
"""Post-rewire smoke tests for dependent web mapping applications.

This script validates that dependent app items no longer reference legacy web map IDs
and (optionally) now reference the migrated replacement IDs.
"""

from __future__ import annotations

import argparse
import csv
import json
import warnings
from pathlib import Path
from typing import Any, Dict, Iterable, List, Set

from migrate_legacy_webmaps import (
    InsecureRequestWarning,
    _filtered_showwarning,
    connect_gis,
    find_dependent_apps,
    output_path,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Smoke test dependent apps after web map rewiring.")
    parser.add_argument("--auth-mode", choices=["keyring", "profile", "home"], default="keyring")
    parser.add_argument("--username", default=None)
    parser.add_argument("--password", default=None)
    parser.add_argument("--profile", default=None)
    parser.add_argument("--mapping-report", required=True, help="CSV from rebuild_legacy_webmaps.py")
    parser.add_argument(
        "--rewire-report",
        default="",
        help="Optional CSV from rewire_dependent_apps.py. If omitted, dependents are rediscovered.",
    )
    parser.add_argument("--app-scan-mode", choices=["targeted", "full"], default="targeted")
    parser.add_argument("--max-app-items", type=int, default=5000)
    parser.add_argument("--heartbeat-every", type=int, default=100)
    parser.add_argument("--report-csv", default="legacy_webmap_rewire_smoke_test.csv")
    return parser.parse_args()


def load_csv_rows(path: Path) -> List[Dict[str, str]]:
    with path.open("r", newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def load_mapping_rows(path: Path) -> List[Dict[str, str]]:
    rows = load_csv_rows(path)
    out: List[Dict[str, str]] = []
    for row in rows:
        status = (row.get("status") or "").strip()
        source_item_id = (row.get("source_item_id") or "").strip()
        new_item_id = (row.get("new_item_id") or "").strip()
        if status not in {"OK", "DRY_RUN"}:
            continue
        if not source_item_id or not new_item_id:
            continue
        out.append({"source_item_id": source_item_id, "new_item_id": new_item_id})
    return out


def load_rewire_targets(path: Path) -> Dict[str, Set[str]]:
    rows = load_csv_rows(path)
    out: Dict[str, Set[str]] = {}
    for row in rows:
        source_id = (row.get("source_item_id") or "").strip()
        app_id = (row.get("dependent_app_id") or "").strip()
        if not source_id or not app_id:
            continue
        out.setdefault(source_id, set()).add(app_id)
    return out


def gather_app_targets(
    gis,
    mappings: Iterable[Dict[str, str]],
    rewire_targets: Dict[str, Set[str]],
    app_scan_mode: str,
    max_app_items: int,
    heartbeat_every: int,
) -> Dict[str, Set[str]]:
    out: Dict[str, Set[str]] = {}
    for m in mappings:
        source_id = m["source_item_id"]
        if source_id in rewire_targets:
            out[source_id] = set(rewire_targets[source_id])
            continue

        dependents = find_dependent_apps(
            gis,
            source_id,
            max_items=max_app_items,
            scan_mode=app_scan_mode,
            heartbeat_every=heartbeat_every,
        )
        out[source_id] = {app.itemid for app in dependents if getattr(app, "itemid", "")}
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
                "url_contains_old",
                "data_contains_old",
                "url_contains_new",
                "data_contains_new",
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
        raise RuntimeError("No successful mapping rows found in mapping report")

    rewire_targets: Dict[str, Set[str]] = {}
    if args.rewire_report:
        rewire_path = Path(args.rewire_report)
        if not rewire_path.is_absolute():
            rewire_path = output_path(args.rewire_report)
        if not rewire_path.exists():
            raise RuntimeError(f"Rewire report not found: {rewire_path}")
        rewire_targets = load_rewire_targets(rewire_path)

    gis = connect_gis(args)
    targets = gather_app_targets(
        gis=gis,
        mappings=mappings,
        rewire_targets=rewire_targets,
        app_scan_mode=args.app_scan_mode,
        max_app_items=args.max_app_items,
        heartbeat_every=args.heartbeat_every,
    )

    report_rows: List[Dict[str, Any]] = []

    for m in mappings:
        source_id = m["source_item_id"]
        new_id = m["new_item_id"]
        app_ids = sorted(targets.get(source_id, set()))

        if not app_ids:
            report_rows.append(
                {
                    "source_item_id": source_id,
                    "new_item_id": new_id,
                    "dependent_app_id": "",
                    "dependent_app_title": "",
                    "url_contains_old": "",
                    "data_contains_old": "",
                    "url_contains_new": "",
                    "data_contains_new": "",
                    "status": "NO_DEPENDENT_APPS_FOUND",
                    "error": "",
                }
            )
            continue

        for app_id in app_ids:
            row = {
                "source_item_id": source_id,
                "new_item_id": new_id,
                "dependent_app_id": app_id,
                "dependent_app_title": "",
                "url_contains_old": "",
                "data_contains_old": "",
                "url_contains_new": "",
                "data_contains_new": "",
                "status": "",
                "error": "",
            }

            try:
                app = gis.content.get(app_id)
                if app is None:
                    row["status"] = "APP_NOT_FOUND"
                    report_rows.append(row)
                    continue

                row["dependent_app_title"] = app.title or ""

                app_url = str(app.url or "")
                data_str = ""
                data = app.get_data(try_json=True)
                if data is not None:
                    data_str = json.dumps(data, sort_keys=True)

                url_contains_old = source_id in app_url
                data_contains_old = source_id in data_str
                url_contains_new = new_id in app_url
                data_contains_new = new_id in data_str

                row["url_contains_old"] = str(url_contains_old)
                row["data_contains_old"] = str(data_contains_old)
                row["url_contains_new"] = str(url_contains_new)
                row["data_contains_new"] = str(data_contains_new)

                if url_contains_old or data_contains_old:
                    row["status"] = "FAIL_OLD_ID_PRESENT"
                elif url_contains_new or data_contains_new:
                    row["status"] = "PASS"
                else:
                    row["status"] = "WARN_NO_ID_REFERENCE"

            except Exception as ex:
                row["status"] = "ERROR"
                row["error"] = str(ex)

            report_rows.append(row)

    report_path = output_path(args.report_csv)
    write_report(report_path, report_rows)

    pass_count = sum(1 for r in report_rows if r["status"] == "PASS")
    fail_count = sum(1 for r in report_rows if r["status"] == "FAIL_OLD_ID_PRESENT")
    warn_count = sum(1 for r in report_rows if r["status"].startswith("WARN_"))
    error_count = sum(1 for r in report_rows if r["status"] in {"ERROR", "APP_NOT_FOUND"})

    print(f"Wrote smoke-test report: {report_path}")
    print(
        "Smoke-test summary: "
        f"pass={pass_count}, fail_old_id={fail_count}, warn={warn_count}, error={error_count}, total={len(report_rows)}"
    )


if __name__ == "__main__":
    main()
