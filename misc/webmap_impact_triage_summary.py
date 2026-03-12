#!/usr/bin/env python
"""Generate a practical triage summary from hosted webmap impact audit data.

Outputs:
- Markdown summary for quick review.
- CSV with prioritized legacy-webmap actions.
"""

from __future__ import annotations

import argparse
import csv
import getpass
from collections import defaultdict
from pathlib import Path
from typing import Dict, Iterable, List, Optional

from arcgis.gis import GIS


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Create actionable triage outputs from a hosted impact table."
    )
    parser.add_argument("--source-item-id", required=True, help="Hosted table/feature service item id.")
    parser.add_argument(
        "--auth-mode",
        choices=["keyring", "profile", "home"],
        default="keyring",
        help="ArcGIS auth mode.",
    )
    parser.add_argument("--username", default=None, help="Username for keyring/password auth.")
    parser.add_argument("--password", default=None, help="Explicit password override.")
    parser.add_argument("--profile", default=None, help="Saved ArcGIS profile for --auth-mode profile.")
    parser.add_argument("--layer-index", type=int, default=0, help="Table/layer index in source item.")
    parser.add_argument("--top-n-owners", type=int, default=20, help="Number of owner rows in markdown summary.")
    parser.add_argument(
        "--summary-md",
        default="legacy_webmaps_triage_summary.md",
        help="Markdown output path (relative paths are written under misc/).",
    )
    parser.add_argument(
        "--actions-csv",
        default="legacy_webmaps_actions.csv",
        help="CSV output path (relative paths are written under misc/).",
    )
    return parser.parse_args()


def output_path(value: str) -> Path:
    p = Path(value)
    if p.is_absolute():
        return p
    return Path(__file__).resolve().parent / p


def connect_gis(args: argparse.Namespace) -> GIS:
    if args.auth_mode == "home":
        return GIS("home")
    if args.auth_mode == "profile":
        if not args.profile:
            raise ValueError("--profile is required when --auth-mode profile")
        return GIS(profile=args.profile)

    username = (args.username or "").strip()
    if not username:
        username = input("Enter your ArcGIS Online username: ").strip()
    if not username:
        raise ValueError("Username is required for keyring auth")

    if args.password:
        return GIS(username=username, password=args.password)

    try:
        import keyring  # type: ignore
    except Exception:
        keyring = None

    if keyring is not None:
        credential = keyring.get_credential("system", username)
        if credential is None:
            raise RuntimeError(f"'{username}' not found in keyring service 'system'.")
        password = keyring.get_password("system", username)
        if not password:
            raise RuntimeError(f"Password for '{username}' not found in keyring service 'system'.")
        return GIS(username=username, password=password)

    password = getpass.getpass("Enter password for ArcGIS Online username: ")
    return GIS(username=username, password=password)


def get_table(item, layer_index: int):
    if getattr(item, "tables", None) and layer_index < len(item.tables):
        return item.tables[layer_index]
    if getattr(item, "layers", None) and layer_index < len(item.layers):
        return item.layers[layer_index]
    raise RuntimeError("Unable to resolve table/layer at requested index")


def pick_field(field_names: set[str], *candidates: str) -> Optional[str]:
    for name in candidates:
        if name in field_names:
            return name
    return None


def as_int(value) -> int:
    try:
        return int(value or 0)
    except Exception:
        return 0


def action_for(record: Dict[str, object]) -> str:
    if not record["is_legacy"]:
        return "NO_ACTION"
    if record["public_exposure"] > 0 and record["priority"] == "HIGH":
        return "MIGRATE_FIRST"
    if record["public_exposure"] > 0 or record["ref_app_count"] >= 6:
        return "MIGRATE_HIGH"
    if record["priority"] in {"HIGH", "MEDIUM"} or record["ref_app_count"] >= 1:
        return "MIGRATE_PLANNED"
    return "REVIEW_OR_ARCHIVE"


def risk_score_for(record: Dict[str, object]) -> int:
    if not record["is_legacy"]:
        return 0
    score = 50
    if record["priority"] == "HIGH":
        score += 35
    elif record["priority"] == "MEDIUM":
        score += 20
    else:
        score += 8
    score += min(record["ref_app_count"], 20) * 2
    score += min(record["public_total_refs"], 20) * 4
    if record["public_exposure"] > 0:
        score += 25
    return score


def collect_records(table, fields: Dict[str, Optional[str]]) -> List[Dict[str, object]]:
    out_fields = [v for v in fields.values() if v]
    if not out_fields:
        raise RuntimeError("No usable fields were detected in the source table")
    fs = table.query(where="1=1", out_fields=",".join(sorted(set(out_fields))), return_geometry=False)
    rows = fs.features if hasattr(fs, "features") else []

    records = []
    for f in rows:
        a = f.attributes or {}
        status = str(a.get(fields["status"], "")).upper() if fields["status"] else ""
        priority = str(a.get(fields["priority"], "")).upper() if fields["priority"] else ""
        ref_app_count = as_int(a.get(fields["ref_app_count"])) if fields["ref_app_count"] else 0
        ref_public_app = as_int(a.get(fields["ref_public_app_count"])) if fields["ref_public_app_count"] else 0
        ref_public_mt = as_int(a.get(fields["ref_public_maptour_count"])) if fields["ref_public_maptour_count"] else 0
        public_total = ref_public_app + ref_public_mt
        rec = {
            "title": str(a.get(fields["title"], "")) if fields["title"] else "",
            "owner": str(a.get(fields["owner"], "")) if fields["owner"] else "",
            "url": str(a.get(fields["url"], "")) if fields["url"] else "",
            "status": status,
            "priority": priority,
            "ref_app_count": ref_app_count,
            "ref_public_app_count": ref_public_app,
            "ref_public_maptour_count": ref_public_mt,
            "public_total_refs": public_total,
            "public_exposure": 1 if public_total > 0 else 0,
            "is_legacy": status == "LEGACY",
        }
        rec["recommended_action"] = action_for(rec)
        rec["risk_score"] = risk_score_for(rec)
        records.append(rec)
    return records


def write_actions_csv(path: Path, records: Iterable[Dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    rows = [r for r in records if r["is_legacy"]]
    rows.sort(key=lambda r: (-int(r["risk_score"]), -int(r["public_total_refs"]), -int(r["ref_app_count"])))
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=[
                "owner",
                "title",
                "status",
                "priority",
                "ref_app_count",
                "ref_public_app_count",
                "ref_public_maptour_count",
                "public_total_refs",
                "risk_score",
                "recommended_action",
                "url",
            ],
        )
        writer.writeheader()
        for r in rows:
            writer.writerow({k: r.get(k, "") for k in writer.fieldnames})


def summarize(records: List[Dict[str, object]], top_n_owners: int) -> str:
    total = len(records)
    legacy = [r for r in records if r["is_legacy"]]
    modern_or_other = total - len(legacy)
    public_legacy = sum(1 for r in legacy if r["public_exposure"] > 0)
    migrate_first = sum(1 for r in legacy if r["recommended_action"] == "MIGRATE_FIRST")
    migrate_high = sum(1 for r in legacy if r["recommended_action"] == "MIGRATE_HIGH")
    planned = sum(1 for r in legacy if r["recommended_action"] == "MIGRATE_PLANNED")
    archive = sum(1 for r in legacy if r["recommended_action"] == "REVIEW_OR_ARCHIVE")

    owner_rollup: Dict[str, Dict[str, int]] = defaultdict(lambda: {"legacy": 0, "public": 0, "score": 0})
    for r in legacy:
        owner = str(r["owner"] or "(no owner)")
        owner_rollup[owner]["legacy"] += 1
        owner_rollup[owner]["public"] += int(r["public_exposure"])
        owner_rollup[owner]["score"] += int(r["risk_score"])

    top = sorted(owner_rollup.items(), key=lambda kv: (-kv[1]["score"], -kv[1]["public"], -kv[1]["legacy"]))[:top_n_owners]

    lines = []
    lines.append("# Legacy Webmaps Triage Summary")
    lines.append("")
    lines.append("## Headline Counts")
    lines.append(f"- Total rows scanned: {total}")
    lines.append(f"- Legacy webmaps: {len(legacy)}")
    lines.append(f"- Non-legacy rows: {modern_or_other}")
    lines.append(f"- Legacy with public exposure: {public_legacy}")
    lines.append("")
    lines.append("## Action Buckets")
    lines.append(f"- MIGRATE_FIRST: {migrate_first}")
    lines.append(f"- MIGRATE_HIGH: {migrate_high}")
    lines.append(f"- MIGRATE_PLANNED: {planned}")
    lines.append(f"- REVIEW_OR_ARCHIVE: {archive}")
    lines.append("")
    lines.append("## Top Owners by Risk Load")
    lines.append("")
    lines.append("| Owner | Legacy Count | Public Exposure Count | Risk Score Sum |")
    lines.append("|---|---:|---:|---:|")
    for owner, agg in top:
        lines.append(f"| {owner} | {agg['legacy']} | {agg['public']} | {agg['score']} |")
    lines.append("")
    lines.append("## Notes")
    lines.append("- Use the companion actions CSV to drive owner outreach and migration sequencing.")
    lines.append("- Risk score is a heuristic for sorting, not a compliance metric.")
    return "\n".join(lines)


def write_summary_md(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def main() -> None:
    args = parse_args()
    gis = connect_gis(args)

    item = gis.content.get(args.source_item_id)
    if item is None:
        raise RuntimeError(f"Item not found: {args.source_item_id}")
    table = get_table(item, args.layer_index)

    field_names = {f.get("name") for f in table.properties.fields if f.get("name")}
    fields = {
        "title": pick_field(field_names, "title", "map_title", "name"),
        "owner": pick_field(field_names, "owner", "username"),
        "url": pick_field(field_names, "url", "item_url"),
        "status": pick_field(field_names, "status", "version_status"),
        "priority": pick_field(field_names, "migration_priority", "priority"),
        "ref_app_count": pick_field(field_names, "ref_app_count", "app_ref_count"),
        "ref_public_app_count": pick_field(field_names, "ref_public_app_count"),
        "ref_public_maptour_count": pick_field(field_names, "ref_public_maptour_count"),
    }

    records = collect_records(table, fields)

    summary_md_path = output_path(args.summary_md)
    actions_csv_path = output_path(args.actions_csv)

    write_actions_csv(actions_csv_path, records)
    write_summary_md(summary_md_path, summarize(records, args.top_n_owners))

    print(f"Wrote summary markdown: {summary_md_path}")
    print(f"Wrote prioritized actions CSV: {actions_csv_path}")


if __name__ == "__main__":
    main()
