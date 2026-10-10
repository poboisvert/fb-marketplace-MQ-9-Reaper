"""Send a short Pushover alert when a monitor finds new listings."""

import os
import sys

import httpx

PUSHOVER_URL = "https://api.pushover.net/1/messages.json"
RUNS_URL = "https://poboisvert.github.io/fb-marketplace-MQ-9-Reaper/runs/index.html"
MAX_LINES = 5
MAX_MESSAGE = 1024


def notify_new_listings(name: str, listings: list) -> None:
    """Post one Pushover message for new listings. Empty checks stay quiet."""
    if not listings:
        return
    token = os.environ.get("PUSHOVER_TOKEN", "").strip()
    user = os.environ.get("PUSHOVER_USER", "").strip()
    if not token or not user:
        print(
            "Pushover skipped: set PUSHOVER_TOKEN and PUSHOVER_USER.",
            file=sys.stderr,
        )
        return
    try:
        response = httpx.post(
            PUSHOVER_URL,
            data=_payload(name, listings, token, user),
            timeout=20,
        )
        response.raise_for_status()
    except Exception as exc:
        print(f"Pushover failed: {exc}", file=sys.stderr)


def _payload(name: str, listings: list, token: str, user: str) -> dict[str, str]:
    data = {
        "token": token,
        "user": user,
        "title": f"{name}: {len(listings)} new",
        "message": _message(listings),
        "url": RUNS_URL,
    }
    device = os.environ.get("PUSHOVER_DEVICE", "").strip()
    if device:
        data["device"] = device
    return data


def _message(listings: list) -> str:
    lines = []
    for listing in listings[:MAX_LINES]:
        price = _field(listing, "price")
        title = _field(listing, "title")
        location = _field(listing, "location")
        lines.append(f"{price} {title} — {location}".strip())
    extra = len(listings) - MAX_LINES
    if extra > 0:
        lines.append(f"+ {extra} more")
    return "\n".join(lines)[:MAX_MESSAGE]


def _field(listing, name: str) -> str:
    if isinstance(listing, dict):
        return str(listing.get(name) or "")
    return str(getattr(listing, name, "") or "")
