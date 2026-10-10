"""Confirm the Slack bot token can post to the configured channel."""

import os

import httpx
import pytest

from facebook_marketplace_mcp.notify import SLACK_URL, _load_env_file

AUTH_URL = "https://slack.com/api/auth.test"


def _slack_env() -> tuple[str, str]:
    _load_env_file()
    token = os.environ.get("SLACK_BOT_TOKEN", "").strip()
    channel = os.environ.get("SLACK_CHANNEL", "").strip()
    if not token or not channel:
        pytest.skip("Set SLACK_BOT_TOKEN and SLACK_CHANNEL in server/.env")
    if not token.startswith("xoxb-"):
        raise AssertionError("SLACK_BOT_TOKEN must be a bot token (xoxb-)")
    return token, channel


def _post(url: str, token: str, payload: dict) -> dict:
    response = httpx.post(
        url,
        headers={"Authorization": f"Bearer {token}"},
        json=payload,
        timeout=20,
    )
    response.raise_for_status()
    body = response.json()
    assert body.get("ok") is True, body.get("error", "Slack request failed")
    return body


def test_slack_bot_can_post():
    token, channel = _slack_env()

    auth = _post(AUTH_URL, token, {})
    assert auth.get("user"), "Slack auth.test did not return the bot user"

    posted = _post(
        SLACK_URL,
        token,
        {
            "channel": channel,
            "text": "Marketplace monitor test. Slack setup is confirmed.",
        },
    )
    assert posted.get("channel") == channel
