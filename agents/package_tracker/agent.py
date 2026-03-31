"""
package_tracker agent — Monitor package shipments and post status updates.

Polls UPS, FedEx, and USPS tracking APIs. Stores tracking numbers and
last-known status in SQLite. Posts to #packages when status changes.

LLM usage: Optional — only for parsing unstructured carrier status text.

Usage:
    python agent.py                          # poll all tracked packages
    python agent.py --dry-run                # print updates, no Discord post
    python agent.py --add <carrier> <number> [description]
    python agent.py --remove <tracking_number>
    python agent.py --list                   # show all tracked packages
"""

import argparse
import json
import logging
import logging.handlers
import os
import pathlib
import sqlite3
import sys
from datetime import datetime, timezone
from typing import Any

import httpx
import yaml
from dotenv import load_dotenv

PROJECT_ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))

from lib import llm
from lib.discord_out import post_error, send_embed, send_message
from lib.schemas import PackageStatusResponse

load_dotenv(PROJECT_ROOT / ".env")

LOG_DIR = PROJECT_ROOT / "logs"
LOG_DIR.mkdir(exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.handlers.RotatingFileHandler(
            LOG_DIR / "package_tracker.log",
            maxBytes=5 * 1024 * 1024,
            backupCount=3,
        ),
    ],
)
logger = logging.getLogger("package_tracker")

AGENT = "package_tracker"

CARRIER_NAMES = {"ups", "fedex", "usps"}


# ---------------------------------------------------------------------------
# SQLite persistence
# ---------------------------------------------------------------------------

def init_db(db_path: str) -> sqlite3.Connection:
    path = pathlib.Path(db_path).expanduser()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), check_same_thread=False)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS packages (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            tracking_number TEXT NOT NULL UNIQUE,
            carrier         TEXT NOT NULL,
            description     TEXT DEFAULT '',
            last_status     TEXT DEFAULT '',
            last_location   TEXT DEFAULT '',
            estimated_delivery TEXT DEFAULT '',
            delivered       INTEGER DEFAULT 0,
            added_at        TEXT NOT NULL,
            updated_at      TEXT NOT NULL
        )
    """)
    conn.commit()
    return conn


def add_package(
    conn: sqlite3.Connection,
    tracking_number: str,
    carrier: str,
    description: str = "",
) -> int:
    now = datetime.now(timezone.utc).isoformat()
    cur = conn.execute(
        """INSERT INTO packages (tracking_number, carrier, description, added_at, updated_at)
           VALUES (?, ?, ?, ?, ?)""",
        (tracking_number.strip(), carrier.lower(), description, now, now),
    )
    conn.commit()
    return cur.lastrowid


def remove_package(conn: sqlite3.Connection, tracking_number: str) -> bool:
    cur = conn.execute(
        "DELETE FROM packages WHERE tracking_number=?",
        (tracking_number.strip(),),
    )
    conn.commit()
    return cur.rowcount > 0


def list_packages(conn: sqlite3.Connection, include_delivered: bool = False) -> list[dict[str, Any]]:
    query = "SELECT id, tracking_number, carrier, description, last_status, last_location, estimated_delivery, delivered, updated_at FROM packages"
    if not include_delivered:
        query += " WHERE delivered=0"
    query += " ORDER BY added_at DESC"
    cur = conn.execute(query)
    cols = ["id", "tracking_number", "carrier", "description", "last_status",
            "last_location", "estimated_delivery", "delivered", "updated_at"]
    return [dict(zip(cols, row)) for row in cur]


def update_package_status(
    conn: sqlite3.Connection,
    tracking_number: str,
    status: str,
    location: str,
    estimated_delivery: str,
    delivered: bool,
) -> None:
    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        """UPDATE packages
           SET last_status=?, last_location=?, estimated_delivery=?, delivered=?, updated_at=?
           WHERE tracking_number=?""",
        (status, location, estimated_delivery, int(delivered), now, tracking_number),
    )
    conn.commit()


# ---------------------------------------------------------------------------
# Carrier API clients
# ---------------------------------------------------------------------------

def _get_ups_token() -> str | None:
    """OAuth2 client credentials flow for UPS API."""
    client_id = os.environ.get("UPS_CLIENT_ID", "")
    client_secret = os.environ.get("UPS_CLIENT_SECRET", "")
    if not client_id or not client_secret:
        return None
    try:
        with httpx.Client(timeout=15.0) as client:
            resp = client.post(
                "https://onlinetools.ups.com/security/v1/oauth/token",
                data={"grant_type": "client_credentials"},
                auth=(client_id, client_secret),
            )
            resp.raise_for_status()
            return resp.json()["access_token"]
    except Exception as exc:
        logger.error("UPS OAuth failed: %s", exc)
        return None


def track_ups(tracking_number: str) -> dict[str, Any] | None:
    token = _get_ups_token()
    if token is None:
        logger.warning("UPS credentials not configured, skipping")
        return None
    try:
        with httpx.Client(timeout=20.0) as client:
            resp = client.get(
                f"https://onlinetools.ups.com/api/track/v1/details/{tracking_number}",
                headers={
                    "Authorization": f"Bearer {token}",
                    "transId": f"allyx-{tracking_number[:8]}",
                    "transactionSrc": "allyx-agents",
                },
                params={"locale": "en_US"},
            )
            resp.raise_for_status()
            data = resp.json()
        shipment = data.get("trackResponse", {}).get("shipment", [{}])[0]
        package = shipment.get("package", [{}])[0]
        activity = package.get("activity", [{}])[0]
        status_desc = activity.get("status", {}).get("description", "Unknown")
        location_parts = activity.get("location", {}).get("address", {})
        location = ", ".join(filter(None, [
            location_parts.get("city", ""),
            location_parts.get("stateProvince", ""),
            location_parts.get("countryCode", ""),
        ]))
        delivery_date = package.get("deliveryDate", [{}])
        est_delivery = delivery_date[0].get("date", "") if delivery_date else ""
        delivered = "delivered" in status_desc.lower()
        return {
            "status": status_desc,
            "location": location,
            "estimated_delivery": est_delivery,
            "delivered": delivered,
            "raw": data,
        }
    except Exception as exc:
        logger.error("UPS tracking failed for %s: %s", tracking_number, exc)
        return None


def track_fedex(tracking_number: str) -> dict[str, Any] | None:
    client_id = os.environ.get("FEDEX_CLIENT_ID", "")
    client_secret = os.environ.get("FEDEX_CLIENT_SECRET", "")
    if not client_id or not client_secret:
        logger.warning("FedEx credentials not configured, skipping")
        return None
    try:
        # Get OAuth token
        with httpx.Client(timeout=15.0) as client:
            token_resp = client.post(
                "https://apis.fedex.com/oauth/token",
                data={
                    "grant_type": "client_credentials",
                    "client_id": client_id,
                    "client_secret": client_secret,
                },
            )
            token_resp.raise_for_status()
            token = token_resp.json()["access_token"]

            # Track package
            resp = client.post(
                "https://apis.fedex.com/track/v1/trackingnumbers",
                headers={"Authorization": f"Bearer {token}"},
                json={
                    "trackingInfo": [{"trackingNumberInfo": {"trackingNumber": tracking_number}}],
                    "includeDetailedScans": False,
                },
            )
            resp.raise_for_status()
            data = resp.json()

        results = data.get("output", {}).get("completeTrackResults", [{}])[0]
        track_result = results.get("trackResults", [{}])[0]
        latest_event = track_result.get("latestStatusDetail", {})
        status_desc = latest_event.get("description", "Unknown")
        scan_location = latest_event.get("scanLocation", {})
        location = ", ".join(filter(None, [
            scan_location.get("city", ""),
            scan_location.get("stateOrProvinceCode", ""),
            scan_location.get("countryCode", ""),
        ]))
        est_delivery = ""
        dates = track_result.get("estimatedDeliveryTimeWindow", {})
        if dates.get("window", {}).get("ends"):
            est_delivery = dates["window"]["ends"]
        delivered = track_result.get("latestStatusDetail", {}).get("code", "") == "DL"
        return {
            "status": status_desc,
            "location": location,
            "estimated_delivery": est_delivery,
            "delivered": delivered,
            "raw": data,
        }
    except Exception as exc:
        logger.error("FedEx tracking failed for %s: %s", tracking_number, exc)
        return None


def track_usps(tracking_number: str) -> dict[str, Any] | None:
    user_id = os.environ.get("USPS_USER_ID", "")
    if not user_id:
        logger.warning("USPS credentials not configured, skipping")
        return None
    try:
        xml_request = (
            f'<TrackFieldRequest USERID="{user_id}">'
            f'<TrackID ID="{tracking_number}"></TrackID>'
            f'</TrackFieldRequest>'
        )
        with httpx.Client(timeout=20.0) as client:
            resp = client.get(
                "https://secure.shippingapis.com/ShippingAPI.dll",
                params={"API": "TrackV2", "XML": xml_request},
            )
            resp.raise_for_status()

        # Parse XML response
        import xml.etree.ElementTree as ET
        root = ET.fromstring(resp.text)
        track_info = root.find(".//TrackInfo")
        if track_info is None:
            logger.warning("USPS returned no TrackInfo for %s", tracking_number)
            return None

        summary = track_info.find("TrackSummary")
        if summary is None:
            return None

        event = summary.text or ""
        city = track_info.findtext("DestinationCity", "")
        state = track_info.findtext("DestinationState", "")
        zipcode = track_info.findtext("DestinationZip", "")
        location = ", ".join(filter(None, [city, state, zipcode]))
        est_delivery = track_info.findtext("ExpectedDeliveryDate", "")
        delivered = "delivered" in event.lower()
        return {
            "status": event,
            "location": location,
            "estimated_delivery": est_delivery,
            "delivered": delivered,
            "raw": resp.text,
        }
    except Exception as exc:
        logger.error("USPS tracking failed for %s: %s", tracking_number, exc)
        return None


TRACK_FUNCTIONS: dict[str, Any] = {
    "ups": track_ups,
    "fedex": track_fedex,
    "usps": track_usps,
}


def track_package(carrier: str, tracking_number: str) -> dict[str, Any] | None:
    fn = TRACK_FUNCTIONS.get(carrier)
    if fn is None:
        logger.error("Unknown carrier: %s", carrier)
        return None
    return fn(tracking_number)


# ---------------------------------------------------------------------------
# LLM status parsing (optional — for unstructured carrier text)
# ---------------------------------------------------------------------------

def parse_status_with_llm(raw_status: str, timeout: float = 20.0) -> PackageStatusResponse | None:
    task = (
        "Parse this package shipping status into structured data. "
        "Extract the current status, location, whether delivered, "
        "and estimated delivery date if available."
    )
    return llm.query(task, raw_status, PackageStatusResponse, timeout=timeout)


# ---------------------------------------------------------------------------
# Discord formatting
# ---------------------------------------------------------------------------

STATUS_EMOJIS: dict[str, str] = {
    "delivered": ":white_check_mark:",
    "out for delivery": ":truck:",
    "in transit": ":package:",
    "shipped": ":package:",
    "label created": ":label:",
    "exception": ":warning:",
    "unknown": ":grey_question:",
}


def _status_emoji(status: str) -> str:
    status_lower = status.lower()
    for key, emoji in STATUS_EMOJIS.items():
        if key in status_lower:
            return emoji
    return ":package:"


def build_update_embed(
    pkg: dict[str, Any],
    new_status: str,
    new_location: str,
    estimated_delivery: str,
    delivered: bool,
) -> dict[str, Any]:
    emoji = _status_emoji(new_status)
    desc_label = pkg["description"] or pkg["tracking_number"]
    title = f"{emoji} {desc_label}"

    fields = [
        {"name": "Carrier", "value": pkg["carrier"].upper(), "inline": True},
        {"name": "Status", "value": new_status, "inline": True},
    ]
    if new_location:
        fields.append({"name": "Location", "value": new_location, "inline": True})
    if estimated_delivery:
        fields.append({"name": "Est. Delivery", "value": estimated_delivery, "inline": True})
    fields.append({
        "name": "Tracking #",
        "value": f"`{pkg['tracking_number']}`",
        "inline": False,
    })

    color = 0x57F287 if delivered else 0x5865F2  # green if delivered, blurple otherwise
    return {
        "title": title,
        "description": f"{'Delivered!' if delivered else 'Status updated'}",
        "fields": fields,
        "color": color,
    }


# ---------------------------------------------------------------------------
# Main polling logic
# ---------------------------------------------------------------------------

def poll_packages(
    conn: sqlite3.Connection,
    config: dict,
    dry_run: bool = False,
) -> int:
    """
    Poll all active (non-delivered) packages. Post to Discord on status change.
    Returns count of packages with updated status.
    """
    packages = list_packages(conn, include_delivered=False)
    if not packages:
        logger.info("No active packages to track")
        return 0

    updates = 0
    llm_timeout = float(config.get("llm", {}).get("timeout_seconds", 20))
    use_llm = config.get("llm", {}).get("enabled", True)

    for pkg in packages:
        logger.info("Tracking %s/%s (%s)", pkg["carrier"], pkg["tracking_number"], pkg["description"])

        result = track_package(pkg["carrier"], pkg["tracking_number"])
        if result is None:
            logger.warning("No result for %s", pkg["tracking_number"])
            continue

        new_status = result["status"]
        new_location = result.get("location", "")
        est_delivery = result.get("estimated_delivery", "")
        delivered = result.get("delivered", False)

        # If status looks unstructured and LLM is enabled, try to parse it
        if use_llm and len(new_status) > 100:
            parsed = parse_status_with_llm(new_status, timeout=llm_timeout)
            if parsed is not None:
                new_status = parsed.status
                new_location = parsed.location or new_location
                est_delivery = parsed.estimated_delivery or est_delivery
                delivered = parsed.delivered

        # Only post if status actually changed
        if new_status == pkg["last_status"] and not (delivered and not pkg["delivered"]):
            logger.debug("No change for %s", pkg["tracking_number"])
            continue

        logger.info("Status change for %s: %r -> %r", pkg["tracking_number"], pkg["last_status"], new_status)

        update_package_status(
            conn, pkg["tracking_number"],
            new_status, new_location, est_delivery, delivered,
        )

        embed = build_update_embed(pkg, new_status, new_location, est_delivery, delivered)
        send_embed(
            channel="packages",
            title=embed["title"],
            description=embed["description"],
            fields=embed["fields"],
            color=embed["color"],
            dry_run=dry_run,
        )
        updates += 1

    return updates


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def load_config() -> dict:
    config_path = pathlib.Path(__file__).parent / "config.yaml"
    with open(config_path) as f:
        return yaml.safe_load(f)


def main() -> None:
    parser = argparse.ArgumentParser(description="package_tracker agent")
    parser.add_argument("--dry-run", action="store_true", help="Print updates, no Discord post")
    parser.add_argument("--add", nargs="+", metavar=("CARRIER", "NUMBER"),
                        help="Add a package: --add <carrier> <tracking_number> [description]")
    parser.add_argument("--remove", metavar="TRACKING_NUMBER", help="Remove a tracked package")
    parser.add_argument("--list", action="store_true", dest="list_all", help="List all tracked packages")
    args = parser.parse_args()

    config = load_config()
    db_conn = init_db(config["database"]["path"])

    # --- Add a package ---
    if args.add:
        if len(args.add) < 2:
            print("Usage: --add <carrier> <tracking_number> [description]")
            sys.exit(1)
        carrier = args.add[0].lower()
        if carrier not in CARRIER_NAMES:
            print(f"Unknown carrier '{carrier}'. Supported: {', '.join(sorted(CARRIER_NAMES))}")
            sys.exit(1)
        tracking_number = args.add[1]
        description = " ".join(args.add[2:]) if len(args.add) > 2 else ""
        try:
            pkg_id = add_package(db_conn, tracking_number, carrier, description)
            print(f"Added package #{pkg_id}: {carrier.upper()} {tracking_number} ({description or 'no description'})")
        except sqlite3.IntegrityError:
            print(f"Tracking number {tracking_number} is already being tracked.")
            sys.exit(1)
        return

    # --- Remove a package ---
    if args.remove:
        if remove_package(db_conn, args.remove):
            print(f"Removed tracking for {args.remove}")
        else:
            print(f"Tracking number {args.remove} not found.")
            sys.exit(1)
        return

    # --- List packages ---
    if args.list_all:
        packages = list_packages(db_conn, include_delivered=True)
        if not packages:
            print("No tracked packages.")
            return
        for pkg in packages:
            status_icon = "[DELIVERED]" if pkg["delivered"] else "[ACTIVE]   "
            desc = pkg["description"] or "no description"
            print(
                f"  {status_icon} {pkg['carrier'].upper():6s} {pkg['tracking_number']} "
                f"— {desc} — {pkg['last_status'] or 'no status yet'}"
            )
        return

    # --- Default: poll all packages ---
    try:
        updates = poll_packages(db_conn, config, dry_run=args.dry_run)
        logger.info("Polling complete. %d package(s) updated.", updates)
    except Exception as exc:
        logger.exception("package_tracker failed")
        post_error(AGENT, str(exc), dry_run=args.dry_run)


if __name__ == "__main__":
    main()
