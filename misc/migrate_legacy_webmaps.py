#!/usr/bin/env python
"""Bulk-migrate legacy Web Map items to modernized replacements.

Workflow per source item:
1) Read source Web Map JSON.
2) Build a migrated JSON payload.
3) Create a new Web Map item with copied metadata.
4) Optionally rename legacy item title with LEGACY - prefix.
5) Optionally update dependent web mapping applications.
6) Emit CSV report with mapping and warnings.
"""

from __future__ import annotations

import argparse
import csv
import getpass
import json
import socket
import warnings
import urllib.parse
import urllib.request
from copy import deepcopy
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

from arcgis.gis import GIS, ItemProperties

try:
    from urllib3.exceptions import InsecureRequestWarning  # type: ignore
except Exception:
    InsecureRequestWarning = Warning

_ORIGINAL_SHOWWARNING = warnings.showwarning


def _filtered_showwarning(message, category, filename, lineno, file=None, line=None):
    text = str(message)
    if "Unverified HTTPS request is being made to host" in text:
        return
    _ORIGINAL_SHOWWARNING(message, category, filename, lineno, file=file, line=line)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Migrate legacy web maps in bulk.")
    parser.add_argument("--item-ids", default="", help="Comma-separated source web map item IDs.")
    parser.add_argument("--item-ids-file", default="", help="File with one source web map item ID per line.")
    parser.add_argument("--auth-mode", choices=["keyring", "profile", "home"], default="keyring")
    parser.add_argument("--username", default=None)
    parser.add_argument("--password", default=None)
    parser.add_argument("--profile", default=None)
    parser.add_argument(
        "--basemap-source-item-id",
        default="",
        help="Optional modern web map ID whose baseMap object is reused in migrated maps.",
    )
    parser.add_argument(
        "--new-title-prefix",
        default="",
        help="Optional prefix for new migrated map titles.",
    )
    parser.add_argument(
        "--legacy-title-prefix",
        default="LEGACY - ",
        help="Prefix applied to renamed source legacy item titles.",
    )
    parser.add_argument(
        "--rename-legacy",
        action="store_true",
        help="Rename source map title to include legacy prefix after successful migration.",
    )
    parser.add_argument(
        "--update-dependent-apps",
        action="store_true",
        help="Update dependent web mapping application item data and URL references.",
    )
    parser.add_argument(
        "--max-app-items",
        type=int,
        default=5000,
        help="Max web mapping applications to scan for dependencies.",
    )
    parser.add_argument(
        "--app-scan-mode",
        choices=["targeted", "full"],
        default="targeted",
        help="Dependency scan mode: targeted query first (fast) or full org scan.",
    )
    parser.add_argument(
        "--heartbeat-every",
        type=int,
        default=100,
        help="Print progress every N scanned apps during dependency scan (0 disables).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print planned actions but do not create or update items.",
    )
    parser.add_argument(
        "--report-csv",
        default="legacy_webmap_migration_report.csv",
        help="CSV output path. Relative paths are written under misc/.",
    )
    parser.add_argument(
        "--layer-replacements-file",
        default="",
        help=(
            "Optional JSON file defining operational layer URL replacements. "
            "Each rule can match by layer id/url and replace by replacement_url "
            "or replacement_item_id."
        ),
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
        username = input("Enter ArcGIS Online username: ").strip()
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


def parse_item_ids(args: argparse.Namespace) -> List[str]:
    ids: List[str] = []
    for raw in (args.item_ids or "").split(","):
        s = raw.strip()
        if s:
            ids.append(s)

    if args.item_ids_file:
        p = Path(args.item_ids_file)
        if not p.is_absolute():
            p = Path.cwd() / p
        for line in p.read_text(encoding="utf-8").splitlines():
            s = line.strip()
            if s and not s.startswith("#"):
                ids.append(s)

    deduped = []
    seen = set()
    for i in ids:
        if i not in seen:
            deduped.append(i)
            seen.add(i)
    return deduped


def get_basemap_template(gis: GIS, item_id: str) -> Optional[Dict[str, Any]]:
    if not item_id:
        return None
    item = gis.content.get(item_id)
    if item is None:
        raise RuntimeError(f"Basemap source item not found: {item_id}")
    data = item.get_data(try_json=True)
    if not isinstance(data, dict) or "baseMap" not in data:
        raise RuntimeError(f"Basemap source item has no baseMap JSON: {item_id}")
    return deepcopy(data.get("baseMap"))


def _item_to_service_url(item: Any) -> str:
    url = str(getattr(item, "url", "") or "").strip()
    if url:
        return url

    data = item.get_data(try_json=True)
    if isinstance(data, dict):
        direct = str(data.get("url", "") or "").strip()
        if direct:
            return direct

        base_map = data.get("baseMap")
        if isinstance(base_map, dict):
            base_layers = base_map.get("baseMapLayers")
            if isinstance(base_layers, list) and base_layers:
                lyr0 = base_layers[0]
                if isinstance(lyr0, dict):
                    lyr0_url = str(lyr0.get("url", "") or "").strip()
                    if lyr0_url:
                        return lyr0_url

        layers = data.get("layers")
        if isinstance(layers, list) and layers:
            lyr0 = layers[0]
            if isinstance(lyr0, dict):
                lyr0_url = str(lyr0.get("url", "") or "").strip()
                if lyr0_url:
                    return lyr0_url

    return ""


def load_layer_replacements(gis: GIS, path_value: str) -> List[Dict[str, str]]:
    if not path_value:
        return []

    path = Path(path_value)
    if not path.is_absolute():
        path = Path.cwd() / path
    if not path.exists():
        raise RuntimeError(f"Layer replacements file not found: {path}")

    payload = json.loads(path.read_text(encoding="utf-8"))
    rules = payload.get("rules") if isinstance(payload, dict) else payload
    if not isinstance(rules, list):
        raise RuntimeError("Layer replacements file must be a list or an object with 'rules' list")

    out: List[Dict[str, str]] = []
    for idx, raw in enumerate(rules, start=1):
        if not isinstance(raw, dict):
            raise RuntimeError(f"Layer replacement rule #{idx} must be an object")

        match_layer_id = str(raw.get("match_layer_id", "") or "").strip()
        match_url = str(raw.get("match_url", "") or "").strip()
        match_url_contains = str(raw.get("match_url_contains", "") or "").strip()
        replacement_url = str(raw.get("replacement_url", "") or "").strip()
        replacement_item_id = str(raw.get("replacement_item_id", "") or "").strip()

        if not (match_layer_id or match_url or match_url_contains):
            raise RuntimeError(
                f"Layer replacement rule #{idx} must define at least one matcher: "
                "match_layer_id, match_url, or match_url_contains"
            )

        if not replacement_url and replacement_item_id:
            item = gis.content.get(replacement_item_id)
            if item is None:
                raise RuntimeError(
                    f"Layer replacement rule #{idx} replacement_item_id not found: {replacement_item_id}"
                )
            replacement_url = _item_to_service_url(item)
            if not replacement_url:
                raise RuntimeError(
                    f"Layer replacement rule #{idx} item has no resolvable service URL: {replacement_item_id}"
                )

        if not replacement_url:
            raise RuntimeError(
                f"Layer replacement rule #{idx} needs replacement_url or replacement_item_id"
            )

        out.append(
            {
                "match_layer_id": match_layer_id,
                "match_url": match_url,
                "match_url_contains": match_url_contains,
                "replacement_url": replacement_url,
            }
        )

    return out


def normalize_url_for_healthcheck(url: str) -> str:
    if not url:
        return ""
    if url.startswith("http://"):
        url = "https://" + url[len("http://") :]
    parsed = urllib.parse.urlparse(url)
    q = urllib.parse.parse_qs(parsed.query)
    q["f"] = ["json"]
    new_query = urllib.parse.urlencode(q, doseq=True)
    return urllib.parse.urlunparse(parsed._replace(query=new_query))


def check_layer_url(url: str, timeout: float = 6.0) -> Tuple[str, str]:
    if not url:
        return ("MISSING_URL", "Layer URL missing")
    check_url = normalize_url_for_healthcheck(url)
    try:
        req = urllib.request.Request(
            check_url,
            headers={"User-Agent": "legacy-webmap-migrator/1.0"},
            method="GET",
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            code = resp.getcode()
            if code >= 400:
                return ("HTTP_ERROR", f"HTTP {code}")
            body = resp.read(2048).decode("utf-8", errors="ignore").lower()
            if "error" in body and "code" in body:
                return ("SERVICE_ERROR", "Service returned ArcGIS error payload")
            return ("OK", "")
    except urllib.error.HTTPError as ex:
        return ("HTTP_ERROR", f"HTTP {ex.code}")
    except urllib.error.URLError as ex:
        return ("URL_ERROR", str(ex.reason))
    except socket.timeout:
        return ("TIMEOUT", "Timed out")
    except Exception as ex:
        return ("ERROR", str(ex))


def migrate_webmap_json(
    source_data: Dict[str, Any],
    basemap_template: Optional[Dict[str, Any]] = None,
    layer_replacements: Optional[List[Dict[str, str]]] = None,
) -> Tuple[Dict[str, Any], Dict[str, str], List[str]]:
    migrated = deepcopy(source_data)
    warnings: List[str] = []
    layer_id_map: Dict[str, str] = {}

    # Remove legacy widget block unsupported by modern Map Viewer schema.
    if "widgets" in migrated:
        del migrated["widgets"]

    # Ensure modern minimum version value and preserve unknown newer values.
    try:
        old_version = float(str(migrated.get("version", "0")))
    except Exception:
        old_version = 0.0
    if old_version < 2.0:
        migrated["version"] = "2.0"

    if basemap_template is not None:
        migrated["baseMap"] = deepcopy(basemap_template)

    # Build layer-id mapping and service health warnings.
    op_layers = migrated.get("operationalLayers") or []
    if isinstance(op_layers, list):
        for idx, lyr in enumerate(op_layers):
            if not isinstance(lyr, dict):
                continue
            old_id = str(lyr.get("id", "")).strip() or f"operational_{idx}"
            # Keep IDs stable where possible to avoid app breakage.
            new_id = old_id
            lyr["id"] = new_id
            layer_id_map[old_id] = new_id

            layer_url = str(lyr.get("url", "") or "")

            if layer_replacements and layer_url:
                for rule in layer_replacements:
                    match_layer_id = str(rule.get("match_layer_id", "") or "")
                    match_url = str(rule.get("match_url", "") or "")
                    match_url_contains = str(rule.get("match_url_contains", "") or "")
                    is_match = False

                    if match_layer_id and old_id == match_layer_id:
                        is_match = True
                    if match_url and layer_url == match_url:
                        is_match = True
                    if match_url_contains and match_url_contains in layer_url:
                        is_match = True

                    if is_match:
                        new_url = str(rule.get("replacement_url", "") or "")
                        if new_url and new_url != layer_url:
                            lyr["url"] = new_url
                            layer_url = new_url
                            warnings.append(
                                f"Layer '{old_id}' URL replaced programmatically"
                            )
                        break

            if layer_url:
                status, detail = check_layer_url(layer_url)
                if status != "OK":
                    warnings.append(f"Layer '{old_id}' URL check: {status} ({detail})")

            # Ensure featureCollection payload survives as-is for map notes / csv layers.
            if "featureCollection" in lyr and not isinstance(lyr.get("featureCollection"), dict):
                warnings.append(f"Layer '{old_id}' has invalid featureCollection payload")

    return migrated, layer_id_map, warnings


def copy_item_metadata(item, new_title: str) -> Dict[str, Any]:
    tags = item.tags if isinstance(item.tags, list) else []
    return {
        "type": "Web Map",
        "title": new_title,
        "snippet": item.snippet or "",
        "description": item.description or "",
        "tags": ",".join(tags),
        "accessInformation": item.accessInformation or "",
        "licenseInfo": item.licenseInfo or "",
        "culture": item.culture or "en-us",
        "extent": item.extent if item.extent else None,
    }


def replace_ids_in_object(value: Any, id_map: Dict[str, str]) -> Any:
    if isinstance(value, dict):
        return {k: replace_ids_in_object(v, id_map) for k, v in value.items()}
    if isinstance(value, list):
        return [replace_ids_in_object(v, id_map) for v in value]
    if isinstance(value, str):
        out = value
        for old_id, new_id in id_map.items():
            if old_id and new_id and old_id != new_id:
                out = out.replace(old_id, new_id)
        return out
    return value


def update_app_url(url: str, id_map: Dict[str, str]) -> str:
    if not url:
        return url
    parsed = urllib.parse.urlparse(url)
    query = urllib.parse.parse_qs(parsed.query)
    changed = False
    for k, vals in list(query.items()):
        new_vals = []
        for v in vals:
            new_v = id_map.get(v, v)
            if new_v != v:
                changed = True
            new_vals.append(new_v)
        query[k] = new_vals
    if not changed:
        # Also do a plain replace for fragment/hash style URLs.
        new_url = url
        for old_id, new_id in id_map.items():
            if old_id != new_id:
                new_url = new_url.replace(old_id, new_id)
        return new_url
    new_query = urllib.parse.urlencode(query, doseq=True)
    return urllib.parse.urlunparse(parsed._replace(query=new_query))


def find_dependent_apps(
    gis: GIS,
    old_webmap_id: str,
    max_items: int,
    scan_mode: str = "targeted",
    heartbeat_every: int = 100,
) -> List[Any]:
    out_by_id: Dict[str, Any] = {}

    # Fast path: query index for direct ID mention in item metadata.
    if scan_mode == "targeted":
        targeted_query = f'type:"Web Mapping Application" "{old_webmap_id}"'
        targeted = gis.content.advanced_search(query=targeted_query, max_items=max_items).get(
            "results", []
        )
        for app in targeted:
            out_by_id[app.itemid] = app
        print(f"Targeted dependency search found {len(targeted)} candidate apps")
        return list(out_by_id.values())

    # Full scan fallback for deep coverage.
    apps = gis.content.advanced_search(
        query='type:"Web Mapping Application"',
        max_items=max_items,
    ).get("results", [])
    print(f"Full dependency scan candidates: {len(apps)} apps")

    for idx, app in enumerate(apps, start=1):
        hit = False
        try:
            if isinstance(getattr(app, "url", ""), str) and old_webmap_id in (app.url or ""):
                hit = True
            if not hit:
                data = app.get_data(try_json=True)
                if data is not None and old_webmap_id in json.dumps(data):
                    hit = True
        except Exception:
            pass
        if hit:
            out_by_id[app.itemid] = app
        if heartbeat_every and idx % heartbeat_every == 0:
            print(f"Scanned {idx}/{len(apps)} apps, matches={len(out_by_id)}")

    return list(out_by_id.values())


def migrate_one(
    gis: GIS,
    source_item_id: str,
    basemap_template: Optional[Dict[str, Any]],
    new_title_prefix: str,
    legacy_title_prefix: str,
    rename_legacy: bool,
    update_apps: bool,
    max_app_items: int,
    app_scan_mode: str,
    heartbeat_every: int,
    dry_run: bool,
    layer_replacements: Optional[List[Dict[str, str]]] = None,
) -> Dict[str, Any]:
    result: Dict[str, Any] = {
        "source_item_id": source_item_id,
        "source_title": "",
        "new_item_id": "",
        "new_title": "",
        "id_map_json": "",
        "apps_updated": 0,
        "status": "",
        "warnings": "",
        "error": "",
    }
    warnings: List[str] = []

    item = gis.content.get(source_item_id)
    if item is None:
        result["status"] = "FAILED"
        result["error"] = "Source item not found"
        return result
    result["source_title"] = item.title or ""

    if item.type != "Web Map":
        result["status"] = "FAILED"
        result["error"] = f"Item is not Web Map (type={item.type})"
        return result

    data = item.get_data(try_json=True)
    if not isinstance(data, dict):
        result["status"] = "FAILED"
        result["error"] = "Source web map JSON is not an object"
        return result

    migrated_data, layer_id_map, data_warnings = migrate_webmap_json(
        data,
        basemap_template,
        layer_replacements=layer_replacements,
    )
    warnings.extend(data_warnings)

    new_title = f"{new_title_prefix}{item.title}" if new_title_prefix else str(item.title)
    result["new_title"] = new_title

    new_item = None
    if dry_run:
        new_item_id = f"DRYRUN-{source_item_id}"
        result["new_item_id"] = new_item_id
    else:
        folder = item.ownerFolder if getattr(item, "ownerFolder", None) else None
        item_props = ItemProperties(
            title=new_title,
            item_type="Web Map",
            snippet=item.snippet or "",
            description=item.description or "",
            tags=item.tags if isinstance(item.tags, list) else [],
            access_information=item.accessInformation or "",
            license_info=item.licenseInfo or "",
            culture=item.culture or "en-us",
            extent=item.extent if item.extent else None,
        )
        try:
            # Root Folder.add is the most reliable add path in ArcGIS API 2.4.x.
            target_folder = gis.content.folders.get()
            add_job = target_folder.add(
                item_properties=item_props,
                text=json.dumps(migrated_data),
            )
            add_result = add_job.result() if hasattr(add_job, "result") else add_job
        except Exception:
            # Backward-compatible fallback for older API behavior.
            props = copy_item_metadata(item, new_title)
            add_kwargs = {
                "item_properties": {k: v for k, v in props.items() if v is not None},
                "text": json.dumps(migrated_data),
            }
            if folder:
                add_kwargs["folder"] = folder
            else:
                # Avoid content.add root-folder None bug on ArcGIS API 2.4.x.
                raise
            add_result = gis.content.add(**add_kwargs)
        new_item = add_result
        result["new_item_id"] = new_item.itemid

    id_map: Dict[str, str] = {source_item_id: result["new_item_id"]}
    id_map.update(layer_id_map)
    result["id_map_json"] = json.dumps(id_map, sort_keys=True)

    if rename_legacy:
        old_title = item.title or ""
        if not old_title.startswith(legacy_title_prefix):
            legacy_title = f"{legacy_title_prefix}{old_title}"
            if dry_run:
                warnings.append(f"Would rename legacy title to '{legacy_title}'")
            else:
                item.update(item_properties={"title": legacy_title})

    apps_updated = 0
    if update_apps:
        dependents = find_dependent_apps(
            gis,
            source_item_id,
            max_items=max_app_items,
            scan_mode=app_scan_mode,
            heartbeat_every=heartbeat_every,
        )
        print(f"Dependent apps to update for {source_item_id}: {len(dependents)}")
        for app in dependents:
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
                    if dry_run:
                        apps_updated += 1
                    else:
                        props = {"url": new_url}
                        if new_data is not None:
                            props["text"] = json.dumps(new_data)
                        app.update(item_properties=props)
                        apps_updated += 1
            except Exception as ex:
                warnings.append(f"Dependent app {app.itemid} update failed: {ex}")

    result["apps_updated"] = apps_updated
    result["warnings"] = " | ".join(warnings)
    result["status"] = "DRY_RUN" if dry_run else "OK"
    return result


def write_report(path: Path, rows: Iterable[Dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=[
                "source_item_id",
                "source_title",
                "new_item_id",
                "new_title",
                "id_map_json",
                "apps_updated",
                "status",
                "warnings",
                "error",
            ],
        )
        writer.writeheader()
        for r in rows:
            writer.writerow(r)


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
                update_apps=args.update_dependent_apps,
                max_app_items=args.max_app_items,
                app_scan_mode=args.app_scan_mode,
                heartbeat_every=args.heartbeat_every,
                dry_run=args.dry_run,
                layer_replacements=layer_replacements,
            )
        except Exception as ex:
            res = {
                "source_item_id": item_id,
                "source_title": "",
                "new_item_id": "",
                "new_title": "",
                "apps_updated": 0,
                "status": "FAILED",
                "warnings": "",
                "error": str(ex),
            }
        results.append(res)

    report = output_path(args.report_csv)
    write_report(report, results)
    print(f"Wrote migration report: {report}")
    ok = sum(1 for r in results if r.get("status") in {"OK", "DRY_RUN"})
    failed = sum(1 for r in results if r.get("status") == "FAILED")
    print(f"Completed: {ok} succeeded, {failed} failed")


if __name__ == "__main__":
    main()
