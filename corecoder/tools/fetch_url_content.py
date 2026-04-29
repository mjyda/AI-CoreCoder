"""HTTP URL fetch + minimal HTML to text extraction tool.

Used as a stable base capability for temp tools that need to "extract webpage content".
"""

from __future__ import annotations

from dataclasses import dataclass
from html.parser import HTMLParser
import ipaddress
from typing import Any
from urllib.parse import urlparse
import urllib.request as urlrequest
import urllib.error as urlerror
import socket
import re

from .base import Tool


class _HTMLTextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []

    def handle_data(self, data: str) -> None:
        if data and data.strip():
            self.parts.append(data.strip())

    def get_text(self) -> str:
        text = " ".join(self.parts)
        text = re.sub(r"\s+", " ", text).strip()
        return text


def _safe_decode_bytes(raw: bytes, content_type_charset: str | None = None) -> str:
    # Prefer declared charset, then try common fallbacks.
    charsets_to_try: list[str] = []
    if content_type_charset:
        charsets_to_try.append(content_type_charset)
    charsets_to_try.extend(["utf-8", "utf-8-sig", "latin-1", "cp1252"])
    last_err: Exception | None = None
    for cs in charsets_to_try:
        try:
            return raw.decode(cs, errors="replace")
        except Exception as exc:  # pragma: no cover
            last_err = exc
    # Extremely unlikely; fall back to a permissive decode.
    if last_err is None:
        return raw.decode("utf-8", errors="replace")
    return raw.decode("utf-8", errors="replace")


def _url_basic_validate(url: str) -> tuple[str, str | None]:
    text = (url or "").strip()
    if not text:
        raise ValueError("empty url")
    parsed = urlparse(text)
    if parsed.scheme not in ("http", "https"):
        raise ValueError(f"unsupported scheme: {parsed.scheme}")
    if not parsed.netloc:
        raise ValueError("missing host")
    return text, parsed.hostname


def _is_private_or_loopback_host(hostname: str) -> bool:
    # If hostname is already an IP, decide without DNS.
    try:
        ip = ipaddress.ip_address(hostname)
        return (
            ip.is_private
            or ip.is_loopback
            or ip.is_reserved
            or ip.is_link_local
            or ip.is_multicast
        )
    except ValueError:
        pass

    # Resolve DNS to validate target isn't local/internal.
    try:
        resolved = socket.getaddrinfo(hostname, None)
    except Exception:
        # If we can't resolve, be conservative but don't brick normal usage.
        # Return False to allow fetch attempts; failures will be returned as Error: ...
        return False

    for _family, _socktype, _proto, _canonname, sockaddr in resolved:
        try:
            ip = ipaddress.ip_address(sockaddr[0])
        except Exception:
            continue
        if (
            ip.is_private
            or ip.is_loopback
            or ip.is_reserved
            or ip.is_link_local
            or ip.is_multicast
        ):
            return True
    return False


@dataclass(frozen=True)
class _SelfTestPayload:
    key: str
    html: str


_SELF_TEST_PAGES: dict[str, _SelfTestPayload] = {
    "webpage": _SelfTestPayload(
        key="webpage",
        html=(
            "<html><head><title>Self Test Webpage</title></head>"
            "<body>"
            "<h1>Self Test Webpage</h1>"
            "<p>SELF_TEST_WEBPAGE_MARKER</p>"
            "<p>This is a deterministic HTML payload used to validate temp tools "
            "that extract and summarize webpage content.</p>"
            "</body></html>"
        ),
    )
}


class FetchUrlContentTool(Tool):
    name = "fetch_url_content"
    description = "Fetch an http(s) URL and convert HTML to plain text."
    parameters: dict[str, Any] = {
        "type": "object",
        "properties": {
            "url": {
                "type": "string",
                "description": "http(s) URL, or self_test://webpage for deterministic tests.",
            },
            "max_chars": {
                "type": "integer",
                "description": "Max output characters (plain text).",
                "default": 6000,
            },
            "timeout_s": {"type": "number", "description": "HTTP timeout seconds.", "default": 10},
            "user_agent": {
                "type": "string",
                "description": "HTTP User-Agent header.",
                "default": "Mozilla/5.0 (CoreCoder)",
            },
        },
    }

    def execute(self, url: str, max_chars: int = 6000, timeout_s: float = 10, user_agent: str = "Mozilla/5.0 (CoreCoder)") -> str:
        text_url = (url or "").strip()
        if not text_url:
            return "Error: empty url"

        if text_url.startswith("self_test://"):
            key = text_url[len("self_test://") :].strip().lower()
            payload = _SELF_TEST_PAGES.get(key)
            if payload is None:
                return f"Error: unknown self_test page: {key}"

            extractor = _HTMLTextExtractor()
            html = re.sub(
                r"<(script|style)[^>]*>.*?</\1>",
                " ",
                payload.html,
                flags=re.IGNORECASE | re.DOTALL,
            )
            extractor.feed(html)
            out = extractor.get_text()
            return out[: max(1, int(max_chars or 6000))] if out else "Error: self_test html produced empty text"

        try:
            normalized_url, hostname = _url_basic_validate(text_url)
        except Exception as exc:
            return f"Error: invalid url: {exc}"

        if hostname and _is_private_or_loopback_host(hostname):
            return f"Error: refused to fetch private/internal host: {hostname}"

        max_chars = max(1, min(int(max_chars or 6000), 20000))

        headers = {"User-Agent": str(user_agent or "Mozilla/5.0 (CoreCoder)")}
        request = urlrequest.Request(normalized_url, headers=headers, method="GET")

        try:
            with urlrequest.urlopen(request, timeout=float(timeout_s or 10)) as resp:
                content_type = resp.headers.get("Content-Type", "")
                charset = None
                m = re.search(
                    r"charset=([a-zA-Z0-9_\-]+)",
                    content_type,
                    flags=re.IGNORECASE,
                )
                if m:
                    charset = m.group(1)

                # Read bounded response.
                raw = resp.read(max_chars * 3)
                if not raw:
                    return "Error: empty response body"
                html = _safe_decode_bytes(raw, charset)
        except urlerror.HTTPError as exc:
            return f"Error: http_error={exc.code}"
        except urlerror.URLError as exc:
            return f"Error: url_error={exc.reason}"
        except socket.timeout:
            return "Error: timeout"
        except Exception as exc:
            return f"Error: fetch_failed={exc}"

        # HTML -> plain text: remove scripts/styles, then strip tags via HTMLParser.
        html = re.sub(
            r"<(script|style)[^>]*>.*?</\1>",
            " ",
            html,
            flags=re.IGNORECASE | re.DOTALL,
        )
        extractor = _HTMLTextExtractor()
        try:
            extractor.feed(html)
        except Exception:
            # Fallback: very rough tag removal.
            stripped = re.sub(r"<[^>]+>", " ", html)
            out = re.sub(r"\s+", " ", stripped).strip()
            return out[:max_chars] if out else "Error: could not parse html"

        out = extractor.get_text()
        if not out:
            return "Error: extracted text is empty"
        return out[:max_chars]

