"""Send a short Slack message when a monitor finds new listings."""

import os
import sys
from pathlib import Path

import httpx

SLACK_URL = "https://slack.com/api/chat.postMessage"
RUNS_URL = "https://poboisvert.github.io/fb-marketplace-Reaper/runs/index.html"
MAX_LINES = 5
MAX_MESSAGE = 1024
_ENV_FILE = Path(__file__).resolve().parents[2] / ".env"


def _load_env_file() -> None:
    if not _ENV_FILE.exists():
        return
    for line in _ENV_FILE.read_text(encoding="utf-8").splitlines():
        text = line.strip()
        if not text or text.startswith("#") or "=" not in text:
            continue
        key, value = text.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def notify_new_listings(name: str, listings: list) -> None:
    """Post one Slack message for new listings. Empty checks stay quiet."""
    if not listings:
        return
    _load_env_file()
    token = os.environ.get("SLACK_BOT_TOKEN", "").strip()
    channel = os.environ.get("SLACK_CHANNEL", "").strip()
    if not token or not channel:
        print(
            "Slack skipped: set SLACK_BOT_TOKEN and SLACK_CHANNEL.",
            file=sys.stderr,
        )
        return
    try:
        response = httpx.post(
            SLACK_URL,
            headers={"Authorization": f"Bearer {token}"},
            json=_payload(name, listings, channel),
            timeout=20,
        )
        response.raise_for_status()
        body = response.json()
        if not body.get("ok"):
            print(f"Slack failed: {body.get('error', 'unknown')}", file=sys.stderr)
    except Exception as exc:
        print(f"Slack failed: {exc}", file=sys.stderr)


def _payload(name: str, listings: list, channel: str) -> dict[str, str]:
    text = f"*{name}: {len(listings)} new*\n{_message(listings)}\n<{RUNS_URL}|Open listings>"
    return {"channel": channel, "text": text[:MAX_MESSAGE]}


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
    return "\n".join(lines)


def _field(listing, name: str) -> str:
    if isinstance(listing, dict):
        return str(listing.get(name) or "")
    return str(getattr(listing, name, "") or "")
