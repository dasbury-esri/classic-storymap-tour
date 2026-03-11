#!/usr/bin/env python3
"""Audit ArcGIS Online items for Classic Map Tour viewer compatibility.

Supports interactive auth with:
- GIS("home")
- profile name prompt (GIS(profile=...))
- keyring-backed username prompt
"""

from __future__ import annotations

import argparse
import csv
import getpass
import re
import sys
from typing import Any, Dict, List, Optional, Tuple

from arcgis.gis import GIS


APPID_RE = re.compile(r"[?&]appid=([a-f0-9]{32})", re.IGNORECASE)


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
        default=1000,
        help="Maximum items returned by search (default: 1000)",
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


def connect_gis(args: argparse.Namespace) -> GIS:
    # 1) Explicit profile name provided
    if args.profile.strip():
        return GIS(profile=args.profile.strip())

    # 2) Prompt for profile name at runtime
    if args.prompt_profile:
        profile = input("Enter ArcGIS profile name (blank to skip): ").strip()
        if profile:
            return GIS(profile=profile)

    # 3) Try GIS("home")
    try:
        return GIS("home")
    except Exception:
        # 4) Keyring/username fallback
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

    print("Searching for candidate Classic Map Tour items...")
    items = gis.content.search(
        query=args.query,
        max_items=args.max_items,
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
