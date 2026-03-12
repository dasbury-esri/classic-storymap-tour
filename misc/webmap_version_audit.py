#!/usr/bin/env python3
"""Scan ArcGIS Online web maps and flag maps with version < 2.0.

This script is intended for org-wide migration prep now that Map Viewer Classic
is retired.

Auth modes mirror misc/maptour_compatibility_audit.py:
- profile
- home
- keyring
- auto (profile -> home -> keyring)
"""

from __future__ import annotations

import argparse, csv, getpass, json, os, re
from typing import Any, Dict, List, Optional, Set, Tuple

from arcgis.gis import GIS


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Find ArcGIS Online Web Maps with version lower than a threshold."
    )
    parser.add_argument(
        "--portal-url",
        default="https://www.arcgis.com",
        help="Portal URL for username/password login (default: https://www.arcgis.com)",
    )
    parser.add_argument(
        "--auth-mode",
        choices=["auto", "profile", "home", "keyring"],
        default="auto",
        help="Authentication mode (default: auto)",
    )
    parser.add_argument(
        "--profile",
        default="",
        help="Saved ArcGIS API for Python profile name (optional)",
    )
    parser.add_argument(
        "--prompt-profile",
        action="store_true",
        help="Prompt for profile name at runtime",
    )
    parser.add_argument(
        "--username",
        default="",
        help="Username for keyring/password fallback (optional)",
    )
    parser.add_argument(
        "--mode",
        choices=["version", "impact"],
        default="version",
        help="Scan mode: version-only or impact (version + app reference analysis)",
    )
    parser.add_argument(
        "--query",
        default='type:"Web Map"',
        help="Search query override (default: type:\"Web Map\")",
    )
    parser.add_argument(
        "--app-query",
        default='type:"Web Mapping Application"',
        help="Application search query used by impact mode",
    )
    parser.add_argument(
        "--min-version",
        default="2.0",
        help="Minimum supported web map version (default: 2.0)",
    )
    parser.add_argument(
        "--max-items",
        type=int,
        default=50000,
        help="Maximum items to evaluate across all pages (default: 50000)",
    )
    parser.add_argument(
        "--max-app-items",
        type=int,
        default=50000,
        help="Maximum Web Mapping Applications to evaluate in impact mode",
    )
    parser.add_argument(
        "--page-size",
        type=int,
        default=100,
        help="Items requested per advanced_search page (default: 100)",
    )
    parser.add_argument(
        "--outside-org",
        action="store_true",
        help="Include results outside your org (default: org-only)",
    )
    parser.add_argument(
        "--csv",
        default="legacy_webmaps_lt_2_0.csv",
        help="Output CSV path (relative paths are written under misc/)",
    )
    parser.add_argument(
        "--heartbeat-every",
        type=int,
        default=500,
        help="Print progress every N items (0 disables item-level heartbeat)",
    )
    parser.add_argument(
        "--max-ref-ids",
        type=int,
        default=20,
        help="Maximum app IDs listed per row in impact mode (default: 20)",
    )
    return parser.parse_args()


def connect_with_keyring(portal_url: str, username: str = "") -> GIS:
    try:
        import keyring  # type: ignore
    except Exception:
        keyring = None

    chosen_username = username.strip()
    if not chosen_username:
        chosen_username = input("Enter your ArcGIS Online username: ").strip()

    if keyring is not None:
        credential = keyring.get_credential("system", chosen_username)
        if credential is None:
            raise RuntimeError(
                f"'{chosen_username}' not found in keyring service 'system'."
            )
        password = keyring.get_password("system", chosen_username)
        if not password:
            raise RuntimeError(
                f"Password for '{chosen_username}' not found in keyring service 'system'."
            )
        return GIS(portal_url, username=chosen_username, password=password)

    password = getpass.getpass("Enter password for ArcGIS Online username: ")
    return GIS(portal_url, username=chosen_username, password=password)


def is_authenticated(gis: GIS) -> bool:
    try:
        return gis.users.me is not None
    except Exception:
        return False


def connect_gis(args: argparse.Namespace) -> GIS:
    auth_mode = (args.auth_mode or "auto").lower()

    def try_profile() -> GIS:
        profile = args.profile.strip()
        if not profile and args.prompt_profile:
            profile = input("Enter ArcGIS profile name (blank to skip): ").strip()
        if not profile:
            raise RuntimeError("No profile provided. Use --profile or --prompt-profile.")
        gis = GIS(profile=profile)
        if not is_authenticated(gis):
            raise RuntimeError(
                f"Profile '{profile}' did not yield an authenticated session."
            )
        return gis

    def try_home() -> GIS:
        gis = GIS("home")
        if not is_authenticated(gis):
            raise RuntimeError("GIS('home') is anonymous in this environment.")
        return gis

    if auth_mode == "profile":
        return try_profile()

    if auth_mode == "home":
        return try_home()

    if auth_mode == "keyring":
        return connect_with_keyring(args.portal_url, args.username)

    if args.profile.strip() or args.prompt_profile:
        try:
            return try_profile()
        except Exception as ex:
            print(f"Profile auth unavailable ({ex}); trying next auth mode.")

    try:
        return try_home()
    except Exception as ex:
        print(f"Home auth unavailable ({ex}); trying keyring/username fallback.")

    return connect_with_keyring(args.portal_url, args.username)


def parse_version(value: Any) -> Optional[Tuple[int, ...]]:
    if value is None:
        return None

    text = str(value).strip()
    if not text:
        return None

    # Extract the leading dotted numeric version only (e.g., "1.11", "2.0", "2").
    m = re.match(r"^(\d+(?:\.\d+)*)", text)
    if not m:
        return None

    return tuple(int(part) for part in m.group(1).split("."))


def normalize_version_tuple(ver: Tuple[int, ...], length: int = 3) -> Tuple[int, ...]:
    if len(ver) >= length:
        return ver[:length]
    return ver + (0,) * (length - len(ver))


def get_webmap_version(item: Any) -> Tuple[str, str]:
    # Returns (version_text, source)
    # The authoritative value is usually in item data JSON ("version").
    version_text = ""
    source = ""

    try:
        data = item.get_data(try_json=True)
    except Exception:
        data = None

    if isinstance(data, dict) and data.get("version") is not None:
        version_text = str(data.get("version"))
        source = "data.version"
        return version_text, source

    # Fallbacks for unusual shapes.
    if isinstance(data, dict) and data.get("authoringAppVersion") is not None:
        version_text = str(data.get("authoringAppVersion"))
        source = "data.authoringAppVersion"
        return version_text, source

    return "", "missing"


def get_candidate_items(
    gis: GIS,
    query: str,
    max_items: int,
    page_size: int,
    outside_org: bool,
    label: str,
) -> List[Any]:
    items: List[Any] = []
    skipped_item_fetch = 0

    org_query = query
    if not outside_org:
        org_id = str(getattr(gis.properties, "id", "") or "")
        if org_id:
            org_query = f"({query}) AND orgid:{org_id}"

    start = 1
    page_size = max(1, min(int(page_size), 100))
    page_no = 0

    while True:
        if max_items > 0 and len(items) >= max_items:
            break

        remaining = max_items - len(items) if max_items > 0 else page_size
        request_size = min(page_size, remaining) if max_items > 0 else page_size

        response = gis.content.advanced_search(
            query=org_query,
            start=start,
            max_items=request_size,
            as_dict=True,
        )
        page_no += 1

        page_results = response.get("results", []) if isinstance(response, dict) else []
        if not page_results:
            break

        for result in page_results:
            item_id = result.get("id") if isinstance(result, dict) else None
            if not item_id:
                continue
            try:
                item_obj = gis.content.get(item_id)
            except KeyboardInterrupt:
                raise
            except Exception:
                skipped_item_fetch += 1
                continue

            if item_obj is not None:
                items.append(item_obj)

        next_start = -1
        if isinstance(response, dict):
            next_start = int(response.get("nextStart", -1) or -1)

        print(
            f"[progress] {label} search page={page_no} fetched={len(page_results)} items",
            flush=True,
        )

        if next_start <= 0:
            break

        start = next_start

    if skipped_item_fetch:
        print(
            f"[warning] skipped {skipped_item_fetch} items that failed content.get during {label} search",
            flush=True,
        )

    return items


def is_hex_id(value: str) -> bool:
    return bool(re.fullmatch(r"[a-fA-F0-9]{32}", value or ""))


def get_url_param(url: str, param: str) -> str:
    if not url:
        return ""
    m = re.search(r"[?&]" + re.escape(param) + r"=([a-fA-F0-9]{32})", url)
    return m.group(1).lower() if m else ""


def is_maptour_app(item: Any) -> bool:
    keywords = [str(k).lower() for k in (getattr(item, "typeKeywords", None) or [])]
    keyword_csv = ",".join(keywords)
    url = str(getattr(item, "url", "") or "").lower()
    return (
        "maptour" in keyword_csv
        or "map tour" in keyword_csv
        or "/apps/maptour/" in url
    )


def extract_webmap_ids_from_data(data: Any) -> Set[str]:
    refs: Set[str] = set()

    def walk(obj: Any, key_hint: str = "") -> None:
        if isinstance(obj, dict):
            for key, value in obj.items():
                key_l = str(key).lower()

                if key_l == "webmap":
                    if isinstance(value, str) and is_hex_id(value):
                        refs.add(value.lower())
                    elif isinstance(value, dict):
                        nested_id = value.get("id")
                        if isinstance(nested_id, str) and is_hex_id(nested_id):
                            refs.add(nested_id.lower())

                walk(value, key_l)
        elif isinstance(obj, list):
            for part in obj:
                walk(part, key_hint)
        elif isinstance(obj, str):
            candidate = obj.strip()
            # Consider URL-style references from arbitrary string fields.
            webmap_param = get_url_param(candidate, "webmap")
            if webmap_param:
                refs.add(webmap_param)

            # If schema key hints indicate a webmap value, accept bare 32-char IDs.
            if key_hint in {"webmap", "webmapid", "web_map", "webmap_id"} and is_hex_id(candidate):
                refs.add(candidate.lower())

    walk(data)
    return refs


def get_app_reference_map(
    apps: List[Any],
    target_webmap_ids: Set[str],
    heartbeat_every: int,
) -> Dict[str, Dict[str, Any]]:
    ref_map: Dict[str, Dict[str, Any]] = {
        wid: {
            "app_ids": set(),
            "public_app_ids": set(),
            "maptour_ids": set(),
            "public_maptour_ids": set(),
        }
        for wid in target_webmap_ids
    }

    heartbeat = max(0, heartbeat_every)
    for idx, app in enumerate(apps, start=1):
        app_id = str(getattr(app, "id", "") or "")
        if not app_id:
            continue

        app_access = str(getattr(app, "access", "") or "")
        is_public = app_access == "public"
        maptour = is_maptour_app(app)

        refs: Set[str] = set()
        app_url = str(getattr(app, "url", "") or "")
        url_ref = get_url_param(app_url, "webmap")
        if url_ref:
            refs.add(url_ref)

        try:
            data = app.get_data(try_json=True)
        except Exception:
            data = None

        if data is not None:
            refs |= extract_webmap_ids_from_data(data)

        for ref_id in refs:
            if ref_id not in ref_map:
                continue

            ref_map[ref_id]["app_ids"].add(app_id)
            if is_public:
                ref_map[ref_id]["public_app_ids"].add(app_id)

            if maptour:
                ref_map[ref_id]["maptour_ids"].add(app_id)
                if is_public:
                    ref_map[ref_id]["public_maptour_ids"].add(app_id)

        if heartbeat > 0 and (idx % heartbeat == 0 or idx == len(apps)):
            print(
                f"[progress] analyzed app refs {idx}/{len(apps)} applications",
                flush=True,
            )

    return ref_map


def enrich_rows_with_impact(
    rows: List[Dict[str, str]],
    ref_map: Dict[str, Dict[str, Any]],
    max_ref_ids: int,
) -> List[Dict[str, str]]:
    def sort_and_clip(values: Set[str]) -> List[str]:
        ordered = sorted(values)
        if max_ref_ids <= 0:
            return ordered
        return ordered[:max_ref_ids]

    enriched: List[Dict[str, str]] = []

    for row in rows:
        item_id = row.get("id", "").lower()
        refs = ref_map.get(item_id, None)

        if refs is None:
            row["ref_app_count"] = "0"
            row["ref_public_app_count"] = "0"
            row["ref_maptour_count"] = "0"
            row["ref_public_maptour_count"] = "0"
            row["ref_app_ids"] = ""
            row["ref_maptour_ids"] = ""
            row["migration_priority"] = "LOW"
            enriched.append(row)
            continue

        app_ids = refs["app_ids"]
        public_app_ids = refs["public_app_ids"]
        maptour_ids = refs["maptour_ids"]
        public_maptour_ids = refs["public_maptour_ids"]

        row["ref_app_count"] = str(len(app_ids))
        row["ref_public_app_count"] = str(len(public_app_ids))
        row["ref_maptour_count"] = str(len(maptour_ids))
        row["ref_public_maptour_count"] = str(len(public_maptour_ids))
        row["ref_app_ids"] = ";".join(sort_and_clip(app_ids))
        row["ref_maptour_ids"] = ";".join(sort_and_clip(maptour_ids))

        if len(public_maptour_ids) > 0 or len(public_app_ids) > 0:
            row["migration_priority"] = "HIGH"
        elif len(app_ids) > 0:
            row["migration_priority"] = "MEDIUM"
        else:
            row["migration_priority"] = "LOW"

        enriched.append(row)

    return enriched


def evaluate_webmap(item: Any, min_ver: Tuple[int, ...]) -> Dict[str, str]:
    item_id = str(getattr(item, "id", "") or "")
    title = str(getattr(item, "title", "") or "")
    owner = str(getattr(item, "owner", "") or "")
    access = str(getattr(item, "access", "") or "")
    item_type = str(getattr(item, "type", "") or "")
    url = f"https://www.arcgis.com/home/item.html?id={item_id}" if item_id else ""
    created = str(getattr(item, "created", "") or "")
    modified = str(getattr(item, "modified", "") or "")

    if item_type != "Web Map":
        return {
            "id": item_id,
            "title": title,
            "owner": owner,
            "access": access,
            "webmap_version": "",
            "version_source": "",
            "status": "SKIP",
            "reason": "wrong_type",
            "url": url,
            "created": created,
            "modified": modified,
        }

    version_text, source = get_webmap_version(item)
    parsed = parse_version(version_text)

    if parsed is None:
        return {
            "id": item_id,
            "title": title,
            "owner": owner,
            "access": access,
            "webmap_version": version_text,
            "version_source": source,
            "status": "UNKNOWN",
            "reason": "missing_or_unparseable_version",
            "url": url,
            "created": created,
            "modified": modified,
        }

    v_norm = normalize_version_tuple(parsed)
    min_norm = normalize_version_tuple(min_ver)

    if v_norm < min_norm:
        status = "LEGACY"
        reason = f"version_lt_{'.'.join(str(p) for p in min_ver)}"
    else:
        status = "MODERN"
        reason = ""

    return {
        "id": item_id,
        "title": title,
        "owner": owner,
        "access": access,
        "webmap_version": version_text,
        "version_source": source,
        "status": status,
        "reason": reason,
        "url": url,
        "created": created,
        "modified": modified,
    }


def write_csv(path: str, rows: List[Dict[str, str]]) -> str:
    output_path = path
    if not os.path.isabs(output_path):
        script_dir = os.path.dirname(os.path.abspath(__file__))
        output_path = os.path.join(script_dir, output_path)

    base_fieldnames = [
        "id",
        "title",
        "owner",
        "access",
        "webmap_version",
        "version_source",
        "status",
        "reason",
        "url",
        "created",
        "modified",
    ]

    extra_fieldnames = sorted(
        {k for row in rows for k in row.keys() if k not in set(base_fieldnames)}
    )
    fieldnames = base_fieldnames + extra_fieldnames

    with open(output_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    return output_path


def print_summary(rows: List[Dict[str, str]], min_version: str) -> None:
    total = len(rows)
    legacy = sum(1 for r in rows if r["status"] == "LEGACY")
    modern = sum(1 for r in rows if r["status"] == "MODERN")
    unknown = sum(1 for r in rows if r["status"] == "UNKNOWN")
    skipped = sum(1 for r in rows if r["status"] == "SKIP")

    print("\nSummary")
    print(f"- Total evaluated: {total}")
    print(f"- LEGACY (< {min_version}): {legacy}")
    print(f"- MODERN (>= {min_version}): {modern}")
    print(f"- UNKNOWN version: {unknown}")
    print(f"- SKIP: {skipped}")


def print_impact_summary(rows: List[Dict[str, str]]) -> None:
    prioritized = [r for r in rows if r.get("status") == "LEGACY"]
    prioritized.sort(key=lambda r: int(r.get("ref_app_count", "0")), reverse=True)

    print("\nImpact summary for LEGACY web maps")
    for row in prioritized[:10]:
        print(
            "- "
            f"{row.get('id', '')} | "
            f"priority={row.get('migration_priority', 'LOW')} | "
            f"apps={row.get('ref_app_count', '0')} | "
            f"public_apps={row.get('ref_public_app_count', '0')} | "
            f"maptour_apps={row.get('ref_maptour_count', '0')}"
        )


def main() -> int:
    args = parse_args()

    min_ver = parse_version(args.min_version)
    if min_ver is None:
        print(f"Invalid --min-version value: {args.min_version}")
        return 2

    try:
        gis = connect_gis(args)
    except Exception as ex:
        print(f"Authentication failed: {ex}")
        return 2

    me = gis.users.me
    if me:
        print(
            f"Logged in as: {me.username} (role: {me.role}, userType: {me.userLicenseTypeId})"
        )
    else:
        print("Logged in anonymously or user profile unavailable.")

    print("Searching for Web Map items (paged advanced_search)...")
    items = get_candidate_items(
        gis=gis,
        query=args.query,
        max_items=args.max_items,
        page_size=args.page_size,
        outside_org=args.outside_org,
        label="webmap",
    )
    print(f"Found {len(items)} candidate items")

    rows: List[Dict[str, str]] = []
    heartbeat_every = max(0, int(args.heartbeat_every))
    legacy_found = 0
    unknown_found = 0
    for idx, item in enumerate(items, start=1):
        result = evaluate_webmap(item, min_ver)
        rows.append(result)
        if result.get("status") == "LEGACY":
            legacy_found += 1
        elif result.get("status") == "UNKNOWN":
            unknown_found += 1

        if heartbeat_every > 0 and (idx % heartbeat_every == 0 or idx == len(items)):
            print(f"[progress] evaluated {idx}/{len(items)} items", flush=True)
            print(
                f"[progress] found {legacy_found} webmaps with version < {args.min_version}",
                flush=True,
            )
            if unknown_found > 0:
                print(
                    f"[progress] found {unknown_found} webmaps with missing/unparseable version",
                    flush=True,
                )

    # Keep CSV focused on actionable rows.
    filtered_rows = [r for r in rows if r["status"] in {"LEGACY", "UNKNOWN"}]

    if args.mode == "impact":
        legacy_ids = {
            str(r.get("id", "")).lower()
            for r in filtered_rows
            if r.get("status") == "LEGACY" and r.get("id")
        }

        if legacy_ids:
            print(
                "Searching for Web Mapping Application items for impact analysis..."
            )
            apps = get_candidate_items(
                gis=gis,
                query=args.app_query,
                max_items=args.max_app_items,
                page_size=args.page_size,
                outside_org=args.outside_org,
                label="webapp",
            )
            print(f"Found {len(apps)} candidate applications")

            ref_map = get_app_reference_map(
                apps=apps,
                target_webmap_ids=legacy_ids,
                heartbeat_every=args.heartbeat_every,
            )
            filtered_rows = enrich_rows_with_impact(
                rows=filtered_rows,
                ref_map=ref_map,
                max_ref_ids=max(0, int(args.max_ref_ids)),
            )
        else:
            print("No LEGACY web maps found; skipping impact analysis.")

    print_summary(rows, args.min_version)
    out_path = write_csv(args.csv, filtered_rows)

    if args.mode == "impact":
        print_impact_summary(filtered_rows)

    print(f"\nCSV written: {out_path}")
    print(f"Rows written (LEGACY + UNKNOWN): {len(filtered_rows)}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
