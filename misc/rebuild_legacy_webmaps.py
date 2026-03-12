#!/usr/bin/env python
"""Step 1: Rebuild legacy web maps and emit a mapping report.

This script intentionally does NOT update dependent apps.
Use rewire_dependent_apps.py for step 2.
"""

from __future__ import annotations

import argparse
import warnings

from migrate_legacy_webmaps import (
    InsecureRequestWarning,
    _filtered_showwarning,
    connect_gis,
    get_basemap_template,
    load_layer_replacements,
    migrate_one,
    output_path,
    parse_item_ids,
    write_report,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Step 1 rebuild of legacy web maps.")
    parser.add_argument("--item-ids", default="", help="Comma-separated source web map item IDs.")
    parser.add_argument("--item-ids-file", default="", help="File with one source web map item ID per line.")
    parser.add_argument("--auth-mode", choices=["keyring", "profile", "home"], default="keyring")
    parser.add_argument("--username", default=None)
    parser.add_argument("--password", default=None)
    parser.add_argument("--profile", default=None)
    parser.add_argument("--basemap-source-item-id", default="")
    parser.add_argument("--new-title-prefix", default="")
    parser.add_argument("--legacy-title-prefix", default="LEGACY - ")
    parser.add_argument("--rename-legacy", action="store_true")
    parser.add_argument(
        "--layer-replacements-file",
        default="",
        help="Optional JSON file with operational layer replacement rules.",
    )
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--report-csv", default="legacy_webmap_rebuild_report.csv")
    return parser.parse_args()


def main() -> None:
    warnings.filterwarnings("ignore", category=InsecureRequestWarning)
    warnings.showwarning = _filtered_showwarning

    args = parse_args()
    item_ids = parse_item_ids(args)
    if not item_ids:
        raise RuntimeError("No item IDs provided. Use --item-ids and/or --item-ids-file")

    gis = connect_gis(args)
    basemap_template = get_basemap_template(gis, args.basemap_source_item_id)
    layer_replacements = load_layer_replacements(gis, args.layer_replacements_file)

    results = []
    for item_id in item_ids:
        try:
            res = migrate_one(
                gis=gis,
                source_item_id=item_id,
                basemap_template=basemap_template,
                new_title_prefix=args.new_title_prefix,
                legacy_title_prefix=args.legacy_title_prefix,
                rename_legacy=args.rename_legacy,
                update_apps=False,
                max_app_items=0,
                app_scan_mode="targeted",
                heartbeat_every=0,
                dry_run=args.dry_run,
                layer_replacements=layer_replacements,
            )
        except Exception as ex:
            res = {
                "source_item_id": item_id,
                "source_title": "",
                "new_item_id": "",
                "new_title": "",
                "id_map_json": "",
                "apps_updated": 0,
                "status": "FAILED",
                "warnings": "",
                "error": str(ex),
            }
        results.append(res)

    report = output_path(args.report_csv)
    write_report(report, results)
    print(f"Wrote rebuild report: {report}")
    ok = sum(1 for r in results if r.get("status") in {"OK", "DRY_RUN"})
    failed = sum(1 for r in results if r.get("status") == "FAILED")
    print(f"Completed: {ok} succeeded, {failed} failed")


if __name__ == "__main__":
    main()
