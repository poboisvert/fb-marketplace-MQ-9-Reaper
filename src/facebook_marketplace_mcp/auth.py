import shutil
import sqlite3
import subprocess
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

CHROME_SALT = b"saltysalt"
CHROME_ITERATIONS = 1003
CHROME_KEY_LENGTH = 16
CHROME_IV = b" " * 16


@dataclass
class FacebookCookie:
    host: str
    name: str
    value: str
    path: str
    expires: int
    secure: bool
    http_only: bool


def get_chrome_password() -> str:
    try:
        result = subprocess.run(
            [
                "security",
                "find-generic-password",
                "-w",
                "-s",
                "Chrome Safe Storage",
                "-a",
                "Chrome",
            ],
            check=True,
            capture_output=True,
            text=True,
        )
    except subprocess.CalledProcessError as exc:
        raise RuntimeError(
            "Failed to get Chrome password from Keychain. "
            "Make sure Chrome is installed and you approve the Keychain prompt."
        ) from exc
    return result.stdout.strip()


def derive_chrome_key(password: str) -> bytes:
    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA1(),
        length=CHROME_KEY_LENGTH,
        salt=CHROME_SALT,
        iterations=CHROME_ITERATIONS,
    )
    return kdf.derive(password.encode())


def decrypt_cookie_value(encrypted: bytes, key: bytes) -> str:
    if not encrypted:
        return ""

    prefix = encrypted[:3].decode("ascii", errors="ignore")
    if prefix != "v10":
        return encrypted.decode("utf-8", errors="replace")

    data = encrypted[3:]
    decryptor = Cipher(algorithms.AES(key), modes.CBC(CHROME_IV)).decryptor()
    decoded = decryptor.update(data) + decryptor.finalize()

    padding = decoded[-1]
    if 0 < padding <= 16:
        decoded = decoded[:-padding]

    # Chrome prepends a 32-byte header to cookie values before encrypting.
    if len(decoded) > 32:
        decoded = decoded[32:]

    return decoded.decode("utf-8", errors="replace")


def cookie_db_path(profile: str = "Default") -> Path:
    return (
        Path.home()
        / "Library/Application Support/Google/Chrome"
        / profile
        / "Cookies"
    )


def extract_chrome_cookies(domain: str, profile: str = "Default") -> list[FacebookCookie]:
    source = cookie_db_path(profile)
    tmp_path = Path(tempfile.gettempdir()) / f"chrome_cookies_{int(time.time() * 1000)}"
    try:
        shutil.copy(source, tmp_path)
    except OSError as exc:
        raise RuntimeError(
            f"Failed to copy Chrome cookie DB from {source}. "
            "Make sure Chrome is installed and the profile exists."
        ) from exc

    password = get_chrome_password()
    key = derive_chrome_key(password)

    try:
        connection = sqlite3.connect(f"file:{tmp_path}?mode=ro", uri=True)
    except sqlite3.Error as exc:
        tmp_path.unlink(missing_ok=True)
        raise RuntimeError(f"Failed to open cookie database at {tmp_path}") from exc

    try:
        rows = connection.execute(
            """
            SELECT host_key, name, value, encrypted_value, path, expires_utc,
                   is_secure, is_httponly
            FROM cookies
            WHERE host_key LIKE ?
            """,
            (f"%{domain}",),
        ).fetchall()
    finally:
        connection.close()
        tmp_path.unlink(missing_ok=True)

    cookies: list[FacebookCookie] = []
    for host, name, value, encrypted_value, path, expires, is_secure, is_httponly in rows:
        cookie_value = value or ""
        if not cookie_value and encrypted_value:
            cookie_value = decrypt_cookie_value(encrypted_value, key)
        cookies.append(
            FacebookCookie(
                host=host,
                name=name,
                value=cookie_value,
                path=path,
                expires=expires,
                secure=bool(is_secure),
                http_only=bool(is_httponly),
            )
        )
    return cookies


def cookies_to_header(cookies: list[FacebookCookie]) -> str:
    parts = []
    for cookie in cookies:
        # Strip non-Latin1 chars — HTTP clients reject them in Cookie headers.
        safe = "".join(char for char in cookie.value if ord(char) <= 255)
        parts.append(f"{cookie.name}={safe}")
    return "; ".join(parts)


def get_cookie_value(cookies: list[FacebookCookie], name: str) -> str | None:
    for cookie in cookies:
        if cookie.name == name:
            return cookie.value
    return None
