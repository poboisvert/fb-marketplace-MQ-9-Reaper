"""Save a PNG preview of a Marketplace listing page."""

import base64
import json
import os
import socket
import struct
import subprocess
import tempfile
import time
import urllib.request
from pathlib import Path

from facebook_marketplace_mcp.auth import extract_chrome_cookies

CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/146.0.0.0 Safari/537.36"
)


class PagePreviewer:
    """One headless Chrome session that screenshots listing URLs."""

    def __init__(self, chrome_profile: str = "Default") -> None:
        self.chrome_profile = chrome_profile
        self._process: subprocess.Popen[bytes] | None = None
        self._socket: socket.socket | None = None
        self._tmpdir: tempfile.TemporaryDirectory[str] | None = None
        self._session_id = ""
        self._next_id = 0

    def __enter__(self) -> "PagePreviewer":
        self.start()
        return self

    def __exit__(self, *_) -> None:
        self.close()

    def start(self) -> None:
        if not Path(CHROME).exists():
            return
        cookies = extract_chrome_cookies("facebook.com", self.chrome_profile)
        port = _free_port()
        self._tmpdir = tempfile.TemporaryDirectory(prefix="fb-preview-")
        self._process = subprocess.Popen(
            [
                CHROME,
                "--headless=new",
                "--disable-gpu",
                "--no-first-run",
                "--no-default-browser-check",
                "--disable-extensions",
                "--disable-blink-features=AutomationControlled",
                f"--remote-debugging-port={port}",
                f"--user-data-dir={self._tmpdir.name}",
                "--window-size=1680,1050",
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        version = _wait_json(f"http://127.0.0.1:{port}/json/version")
        self._socket = _ws_connect(version["webSocketDebuggerUrl"])
        created = self._call("Target.createTarget", {"url": "about:blank"})
        target_id = (created.get("result") or {}).get("targetId")
        attached = self._call(
            "Target.attachToTarget",
            {"targetId": target_id, "flatten": True},
        )
        self._session_id = (attached.get("result") or {}).get("sessionId") or ""
        self._page("Network.enable")
        self._page("Page.enable")
        self._page(
            "Emulation.setUserAgentOverride",
            {"userAgent": USER_AGENT, "platform": "MacIntel"},
        )
        self._page(
            "Emulation.setDeviceMetricsOverride",
            {"width": 1680, "height": 1050, "deviceScaleFactor": 1, "mobile": False},
        )
        for cookie in cookies:
            if not cookie.value:
                continue
            self._page(
                "Network.setCookie",
                {
                    "name": cookie.name,
                    "value": cookie.value,
                    "domain": cookie.host,
                    "path": cookie.path or "/",
                    "secure": cookie.secure,
                    "httpOnly": cookie.http_only,
                    "url": "https://www.facebook.com/",
                },
            )

    def capture(self, url: str, dest: Path) -> bool:
        if self._socket is None or not url:
            return dest.exists()
        self._page("Page.navigate", {"url": url})
        deadline = time.time() + 20
        ready = False
        while time.time() < deadline:
            time.sleep(0.5)
            probed = self._page(
                "Runtime.evaluate",
                {
                    "expression": (
                        "(() => { const text = document.body ? document.body.innerText : ''; "
                        "const photos = document.querySelectorAll('img').length; "
                        "return text.includes('Message') && photos > 2; })()"
                    ),
                    "returnByValue": True,
                },
            )
            ready = bool(((probed.get("result") or {}).get("result") or {}).get("value"))
            if ready:
                break
        if ready:
            time.sleep(0.6)
        shot = self._page(
            "Page.captureScreenshot",
            {
                "format": "png",
                "clip": {"x": 0, "y": 0, "width": 1680, "height": 1050, "scale": 1},
            },
        )
        data = base64.b64decode(((shot.get("result") or {}).get("data") or ""))
        if not data.startswith(b"\x89PNG"):
            return dest.exists()
        dest.write_bytes(data)
        return True

    def close(self) -> None:
        if self._socket is not None:
            self._socket.close()
            self._socket = None
        if self._process is not None:
            self._process.terminate()
            try:
                self._process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self._process.kill()
            self._process = None
        if self._tmpdir is not None:
            self._tmpdir.cleanup()
            self._tmpdir = None

    def _page(self, method: str, params: dict | None = None) -> dict:
        return self._call(method, params, session_id=self._session_id)

    def _call(self, method: str, params: dict | None = None, session_id: str = "") -> dict:
        if self._socket is None:
            return {}
        self._next_id += 1
        message_id = self._next_id
        payload: dict = {"id": message_id, "method": method, "params": params or {}}
        if session_id:
            payload["sessionId"] = session_id
        _ws_send(self._socket, json.dumps(payload))
        while True:
            message = json.loads(_ws_recv(self._socket))
            if message.get("id") == message_id:
                return message


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _wait_json(url: str) -> dict:
    deadline = time.time() + 10
    last_error: Exception | None = None
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=1) as response:
                return json.load(response)
        except Exception as exc:
            last_error = exc
            time.sleep(0.2)
    raise RuntimeError(f"Chrome did not open {url}") from last_error


def _ws_connect(ws_url: str) -> socket.socket:
    host_port, path = ws_url.removeprefix("ws://").split("/", 1)
    host, port = host_port.split(":")
    sock = socket.create_connection((host, int(port)), timeout=10)
    key = base64.b64encode(os.urandom(16)).decode()
    request = (
        f"GET /{path} HTTP/1.1\r\n"
        f"Host: {host}:{port}\r\n"
        "Upgrade: websocket\r\n"
        "Connection: Upgrade\r\n"
        f"Sec-WebSocket-Key: {key}\r\n"
        "Sec-WebSocket-Version: 13\r\n\r\n"
    )
    sock.sendall(request.encode())
    data = b""
    while b"\r\n\r\n" not in data:
        chunk = sock.recv(4096)
        if not chunk:
            break
        data += chunk
    sock.settimeout(20)
    return sock


def _ws_send(sock: socket.socket, text: str) -> None:
    payload = text.encode()
    mask = os.urandom(4)
    header = bytearray([0x81])
    length = len(payload)
    if length < 126:
        header.append(0x80 | length)
    elif length < 65536:
        header.append(0x80 | 126)
        header.extend(struct.pack(">H", length))
    else:
        header.append(0x80 | 127)
        header.extend(struct.pack(">Q", length))
    header.extend(mask)
    masked = bytes(byte ^ mask[index % 4] for index, byte in enumerate(payload))
    sock.sendall(bytes(header) + masked)


def _ws_recv(sock: socket.socket) -> str:
    def read(count: int) -> bytes:
        buf = b""
        while len(buf) < count:
            chunk = sock.recv(count - len(buf))
            if not chunk:
                raise ConnectionError("Chrome closed the preview connection")
            buf += chunk
        return buf

    first, second = read(2)
    opcode = first & 0x0F
    length = second & 0x7F
    if length == 126:
        length = struct.unpack(">H", read(2))[0]
    elif length == 127:
        length = struct.unpack(">Q", read(8))[0]
    if second & 0x80:
        mask = read(4)
        payload = bytes(byte ^ mask[index % 4] for index, byte in enumerate(read(length)))
    else:
        payload = read(length)
    if opcode == 8:
        raise ConnectionError("Chrome closed the preview connection")
    if opcode == 9:
        return _ws_recv(sock)
    if opcode != 1:
        return _ws_recv(sock)
    return payload.decode()
