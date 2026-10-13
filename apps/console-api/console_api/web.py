# Copyright 2026 Shinro SAS. Licensed under the Business Source License 1.1; see LICENSE.
"""console_api.web: the built web application, and the one place the credential reaches a page.

THE CREDENTIAL IS NOT IN THE BUNDLE. The built files are static and identical on every launch; the credential changes on
every launch. It is put into index.html as it is served, inside a data element:

    <script type="application/json" id="kc-console-boot">{"contract_version":"1","credential":"...", ...}</script>

A data element, not executable code, so the page needs no inline script and the response can carry a Content-Security-Policy
that forbids inline script outright. The page reads it once with
JSON.parse(document.getElementById("kc-console-boot").textContent) and keeps the value in memory. index.html is served
no-store: it must never sit in a disk cache, and a reload must get the credential of the CURRENT launch.

STATIC FILES are served from the build directory only. A path is resolved and must still be inside that directory, a
dotted segment or a symbolic link that climbs out is refused before the file is opened, and a name that starts with a dot
is never served.
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Optional

from aiohttp import web

BOOT_ELEMENT_ID = "kc-console-boot"
BOOT_OPEN = f'<script type="application/json" id="{BOOT_ELEMENT_ID}">'
BOOT_CLOSE = "</script>"
INDEX_NAME = "index.html"

CONTENT_SECURITY_POLICY = (
    "default-src 'none'; script-src 'self'; style-src 'self' 'unsafe-inline'; font-src 'self'; "
    "img-src 'self' data:; connect-src 'self'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'"
)
MEDIA_TYPES = {
    ".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8", ".mjs": "text/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8", ".json": "application/json", ".map": "application/json",
    ".woff2": "font/woff2", ".woff": "font/woff", ".ttf": "font/ttf", ".svg": "image/svg+xml", ".ico": "image/x-icon",
    ".png": "image/png", ".webp": "image/webp", ".txt": "text/plain; charset=utf-8",
}


def render_boot(boot: dict) -> str:
    """The data element. '<' is escaped so that no value can close the element early, whatever it holds."""
    payload = json.dumps(boot, separators=(",", ":"), ensure_ascii=True).replace("<", "\\u003c")
    return BOOT_OPEN + payload + BOOT_CLOSE


def inject_boot(html: str, boot: dict) -> str:
    """Replace the build's placeholder element when it has one, otherwise insert before </head> (or at the top)."""
    element = render_boot(boot)
    start = html.find(BOOT_OPEN)
    if start != -1:
        end = html.find(BOOT_CLOSE, start)
        if end != -1:
            return html[:start] + element + html[end + len(BOOT_CLOSE):]
    lowered = html.lower()
    head_end = lowered.find("</head>")
    if head_end != -1:
        return html[:head_end] + element + "\n" + html[head_end:]
    return element + "\n" + html


class WebBuild:
    """The directory holding the built console, or nothing at all when the API is run on its own."""

    def __init__(self, directory: Optional[str]):
        self.root = Path(os.path.abspath(os.path.expanduser(directory))).resolve() if directory else None

    @property
    def attached(self) -> bool:
        return self.root is not None and (self.root / INDEX_NAME).is_file()

    def locate(self, relative: str) -> Optional[Path]:
        """The file a URL path names, or None when it is outside the build, hidden, or not a file."""
        if self.root is None:
            return None
        parts = [part for part in relative.split("/") if part]
        if any(part.startswith(".") for part in parts):
            return None
        try:
            candidate = (self.root / "/".join(parts)).resolve()
        except OSError:
            return None
        if candidate != self.root and self.root not in candidate.parents:
            return None
        return candidate if candidate.is_file() else None

    def index_html(self, boot: dict) -> Optional[str]:
        if self.root is None:
            return None
        path = self.root / INDEX_NAME
        try:
            return inject_boot(path.read_text(encoding="utf-8"), boot)
        except (OSError, UnicodeDecodeError):
            return None


def media_type(path: Path) -> str:
    return MEDIA_TYPES.get(path.suffix.lower(), "application/octet-stream")


# A content hash the bundler appended: a dash and eight base64url characters at the end of the stem. MEASURED against a
# real build: `index-AriOrVho.js`, `index--tvhiCVh.css` (the hash itself starts with a dash) and
# `geist-sans-latin-400-normal-gapTbOY8.woff2` all match, and a stem with no hash does not. A digit is NOT a usable
# signal: two of those three names contain none.
CONTENT_HASHED = re.compile(r".+-[A-Za-z0-9_-]{8}$")


def asset_response(path: Path) -> web.FileResponse:
    """A built asset. Long-lived only for a name that carries a content hash; the build's own index never gets here."""
    cacheable = bool(CONTENT_HASHED.fullmatch(path.stem))
    headers = {
        "Content-Type": media_type(path),
        "X-Content-Type-Options": "nosniff",
        "Cache-Control": "private, max-age=31536000, immutable" if cacheable else "no-cache",
    }
    return web.FileResponse(path, headers=headers)
