#!/usr/bin/env python3
"""Audit ArcGIS Online items for Classic Map Tour viewer compatibility.

Supports interactive auth with:
- GIS("home")
- profile name prompt (GIS(profile=...))
- keyring-backed username prompt

Use --auth-mode to force one path: auto|profile|home|keyring
"""

from __future__ import annotations

import argparse
import csv
import getpass
import re
import sys
from typing import Any, Dict, List

from arcgis.gis import GIS


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Find Classic Map Tour apps and evaluate viewer compatibility."
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
        "--max-items",
        type=int,
        default=10000,
        help="Maximum items to evaluate across all pages (default: 10000)",
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
        "--query",
        default='type:"Web Mapping Application" AND (typekeywords:"MapTour" OR typekeywords:"Map Tour")',
        help="Search query override",
    )
    parser.add_argument(
        "--csv",
        default="",
        help="Optional output CSV path",
    )
    return parser.parse_args()


def get_url_param(url: str, param: str) -> str:
    if not url:
        return ""
    m = re.search(r"[?&]" + re.escape(param) + r"=([^&#]+)", url, flags=re.IGNORECASE)
    return m.group(1) if m else ""


def extract_webmap_id(data: Any) -> str:
    if not isinstance(data, dict):
        return ""

    values = data.get("values") if isinstance(data.get("values"), dict) else {}
    item_data = data.get("itemData") if isinstance(data.get("itemData"), dict) else {}

    # Classic Map Tour common shapes
    if isinstance(values.get("webmap"), dict) and values["webmap"].get("id"):
        return str(values["webmap"]["id"])

    if isinstance(values.get("webmap"), str):
        return values["webmap"]

    if isinstance(data.get("webmap"), str):
        return str(data["webmap"])

    if isinstance(item_data.get("webmap"), str):
        return str(item_data["webmap"])

    return ""


def is_maptour_item(item: Any) -> bool:
    if not item:
        return False

    keywords = [str(k).lower() for k in (getattr(item, "typeKeywords", None) or [])]
    keyword_csv = ",".join(keywords)
    url = str(getattr(item, "url", "") or "").lower()

    return (
        "maptour" in keyword_csv
        or "map tour" in keyword_csv
        or "/apps/maptour/" in url
    )


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

    # auto mode: profile -> home -> keyring
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


def evaluate_item(gis: GIS, item: Any) -> Dict[str, str]:
    item_id = str(getattr(item, "id", "") or "")
    title = str(getattr(item, "title", "") or "")
    owner = str(getattr(item, "owner", "") or "")
    access = str(getattr(item, "access", "") or "")
    item_type = str(getattr(item, "type", "") or "")
    item_url = str(getattr(item, "url", "") or "")

    status = "PASS"
    reason = ""
    resolved_webmap = ""
    nested_appid = ""

    if item_type != "Web Mapping Application":
        status = "FAIL"
        reason = "wrong_type"

    if status == "PASS" and not is_maptour_item(item):
        status = "FAIL"
        reason = "not_maptour_template"

    item_data = None
    if status == "PASS":
        try:
            item_data = item.get_data(try_json=True)
        except Exception:
            item_data = None

        resolved_webmap = extract_webmap_id(item_data)
        if resolved_webmap:
            return {
                "id": item_id,
                "title": title,
                "owner": owner,
                "access": access,
                "status": status,
                "reason": reason,
                "webmap": resolved_webmap,
                "nested_appid": nested_appid,
                "url": item_url,
            }

        nested_appid = get_url_param(item_url, "appid")
        if not nested_appid:
            status = "FAIL"
            reason = "no_webmap_and_no_nested_appid"
        else:
            nested = gis.content.get(nested_appid)
            if nested is None:
                status = "FAIL"
                reason = "nested_item_not_found"
            elif not is_maptour_item(nested):
                status = "FAIL"
                reason = "nested_not_maptour_template"
            else:
                try:
                    nested_data = nested.get_data(try_json=True)
                except Exception:
                    nested_data = None
                resolved_webmap = extract_webmap_id(nested_data)
                if not resolved_webmap:
                    status = "FAIL"
                    reason = "nested_no_webmap"

    return {
        "id": item_id,
        "title": title,
        "owner": owner,
        "access": access,
        "status": status,
        "reason": reason,
        "webmap": resolved_webmap,
        "nested_appid": nested_appid,
        "url": item_url,
    }


def get_candidate_items(
    gis: GIS,
    query: str,
    max_items: int,
    page_size: int,
    outside_org: bool,
) -> List[Any]:
    items: List[Any] = []

    org_query = query
    if not outside_org:
        org_id = str(getattr(gis.properties, "id", "") or "")
        if org_id:
            org_query = f"({query}) AND orgid:{org_id}"

    start = 1
    # ArcGIS REST commonly supports max page size 100; keep request bounded.
    page_size = max(1, min(int(page_size), 100))

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

        page_results = response.get("results", []) if isinstance(response, dict) else []
        if not page_results:
            break

        for result in page_results:
            item_id = result.get("id") if isinstance(result, dict) else None
            if not item_id:
                continue
            item_obj = gis.content.get(item_id)
            if item_obj is not None:
                items.append(item_obj)

        next_start = -1
        if isinstance(response, dict):
            next_start = int(response.get("nextStart", -1) or -1)

        if next_start <= 0:
            break

        start = next_start

    return items


def print_summary(rows: List[Dict[str, str]]) -> None:
    total = len(rows)
    passed = sum(1 for r in rows if r["status"] == "PASS")
    failed = total - passed

    print(f"Total: {total}  PASS: {passed}  FAIL: {failed}")

    if failed:
        print("\nFailures:")
        for row in rows:
            if row["status"] == "FAIL":
                print(f"- {row['id']} | {row['reason']} | {row['title']}")


def write_csv(path: str, rows: List[Dict[str, str]]) -> None:
    if not path:
        return

    fieldnames = [
        "id",
        "title",
        "owner",
        "access",
        "status",
        "reason",
        "webmap",
        "nested_appid",
        "url",
    ]

    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    print(f"\nCSV written: {path}")


def main() -> int:
    args = parse_args()

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

    print("Searching for candidate Classic Map Tour items (paged advanced_search)...")
    items = get_candidate_items(
        gis=gis,
        query=args.query,
        max_items=args.max_items,
        page_size=args.page_size,
        outside_org=args.outside_org,
    )
    print(f"Found {len(items)} items")

    rows: List[Dict[str, str]] = []
    for item in items:
        rows.append(evaluate_item(gis, item))

    print_summary(rows)
    write_csv(args.csv, rows)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
