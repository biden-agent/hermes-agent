"""Safe curl-like HTTP request tool.

This tool intentionally avoids shell execution and local filesystem access.
It only performs HTTP(S) requests and returns the response inline.
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any, Dict, Optional
from urllib.parse import urljoin, urlparse

import httpx

from tools.registry import registry
from tools.url_safety import is_safe_url
from tools.website_policy import check_website_access

logger = logging.getLogger(__name__)

_ALLOWED_METHODS = {"GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"}


def _json_result(payload: Dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False)


def _blocked(message: str, **extra: Any) -> str:
    payload = {"success": False, "error": message, "blocked": True}
    payload.update(extra)
    return _json_result(payload)


def _validate_public_http_url(url: str) -> Optional[str]:
    parsed = urlparse(url)
    scheme = (parsed.scheme or "").lower()
    if scheme not in {"http", "https"}:
        return "Only http:// and https:// URLs are allowed. Local file and other URL schemes are blocked."

    hostname = (parsed.hostname or "").strip().lower().rstrip(".")
    if not hostname:
        return "URL must include a hostname."

    if not is_safe_url(url):
        return f"Blocked private/internal network address or unsafe URL: {hostname}"

    policy_block = check_website_access(url)
    if policy_block:
        return policy_block.get("message") or "Blocked by website policy."
    return None


def _normalize_headers(headers: Any) -> Dict[str, str]:
    if headers is None:
        return {}
    if not isinstance(headers, dict):
        raise ValueError("headers must be an object mapping header names to values")
    if len(headers) > 50:
        raise ValueError("too many headers; maximum is 50")

    normalized: Dict[str, str] = {}
    for key, value in headers.items():
        name = str(key or "").strip()
        text = str(value or "")
        if not name:
            continue
        if any(ch in name for ch in "\r\n:"):
            raise ValueError("header names cannot contain colon or newlines")
        if any(ch in text for ch in "\r\n"):
            raise ValueError("header values cannot contain newlines")
        normalized[name] = text
    return normalized


def _headers_for_result(headers: httpx.Headers) -> Dict[str, str]:
    safe_headers: Dict[str, str] = {}
    for key, value in headers.items():
        if key.lower() in {"set-cookie", "authorization", "proxy-authorization"}:
            continue
        safe_headers[key] = value
    return safe_headers


def _decode_body(content: bytes, response: httpx.Response) -> str:
    encoding = response.encoding or "utf-8"
    try:
        return content.decode(encoding, errors="replace")
    except LookupError:
        return content.decode("utf-8", errors="replace")


def curl_tool(
    url: str,
    method: str = "GET",
    headers: Optional[dict] = None,
    body: Optional[str] = None,
    timeout: int = 30,
    max_bytes: int = 100_000,
    follow_redirects: bool = False,
    max_redirects: int = 5,
) -> str:
    """Perform a safe HTTP(S) request and return a JSON result."""
    url = str(url or "").strip()
    if not url:
        return _blocked("url is required")

    method = str(method or "GET").upper().strip()
    if method not in _ALLOWED_METHODS:
        return _blocked(f"Unsupported HTTP method: {method}")

    try:
        request_headers = _normalize_headers(headers)
    except ValueError as exc:
        return _blocked(str(exc))

    try:
        timeout = max(1, min(int(timeout), 60))
    except (TypeError, ValueError):
        timeout = 30
    try:
        max_bytes = max(1, min(int(max_bytes), 1_000_000))
    except (TypeError, ValueError):
        max_bytes = 100_000
    try:
        max_redirects = max(0, min(int(max_redirects), 10))
    except (TypeError, ValueError):
        max_redirects = 5

    current_url = url
    body_text = None if body is None else str(body)
    redirects = []
    started = time.monotonic()

    try:
        with httpx.Client(timeout=timeout, follow_redirects=False, trust_env=False) as client:
            for _ in range(max_redirects + 1):
                url_error = _validate_public_http_url(current_url)
                if url_error:
                    return _blocked(url_error, url=current_url)

                with client.stream(
                    method,
                    current_url,
                    headers=request_headers,
                    content=body_text.encode("utf-8") if body_text is not None else None,
                ) as response:
                    location = response.headers.get("location")
                    if (
                        follow_redirects
                        and response.status_code in {301, 302, 303, 307, 308}
                        and location
                    ):
                        next_url = urljoin(str(response.url), location)
                        redirects.append(next_url)
                        current_url = next_url
                        if response.status_code == 303 and method not in {"GET", "HEAD"}:
                            method = "GET"
                            body_text = None
                        continue

                    chunks = []
                    total = 0
                    truncated = False
                    for chunk in response.iter_bytes():
                        if total + len(chunk) > max_bytes:
                            remaining = max_bytes - total
                            if remaining > 0:
                                chunks.append(chunk[:remaining])
                            truncated = True
                            break
                        chunks.append(chunk)
                        total += len(chunk)

                    content = b"".join(chunks)
                    return _json_result({
                        "success": True,
                        "url": url,
                        "final_url": str(response.url),
                        "status_code": response.status_code,
                        "reason_phrase": response.reason_phrase,
                        "headers": _headers_for_result(response.headers),
                        "body": _decode_body(content, response),
                        "truncated": truncated,
                        "redirects": redirects,
                        "elapsed_ms": int((time.monotonic() - started) * 1000),
                    })

            return _blocked("Too many redirects", url=current_url, redirects=redirects)
    except httpx.RequestError as exc:
        logger.info("curl tool request failed: %s", exc)
        return _json_result({"success": False, "error": str(exc), "blocked": False, "url": current_url})


CURL_SCHEMA = {
    "name": "curl",
    "description": (
        "Perform a safe HTTP/HTTPS request and return status, headers, and body text. "
        "Only http:// and https:// schemes are allowed; local file and other URL schemes are blocked. "
        "By default, localhost, private/internal networks, CGNAT, reserved, multicast, "
        "unspecified, link-local, and cloud metadata targets are blocked. "
        "HERMES_ALLOW_PRIVATE_URLS=true or security.allow_private_urls allows ordinary "
        "private/internal targets; legacy browser.allow_private_urls is honored only "
        "when security.allow_private_urls is absent. "
        "Cloud metadata targets and the IPv4 169.254.0.0/16 link-local range remain "
        "blocked even when private URLs are allowed. "
        "The response is returned inline and never written to local files."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "url": {"type": "string", "description": "HTTP or HTTPS URL to request."},
            "method": {
                "type": "string",
                "enum": sorted(_ALLOWED_METHODS),
                "description": "HTTP method to use.",
                "default": "GET",
            },
            "headers": {
                "type": "object",
                "description": "Optional request headers as a JSON object.",
                "additionalProperties": {"type": "string"},
            },
            "body": {"type": "string", "description": "Optional UTF-8 request body."},
            "timeout": {
                "type": "integer",
                "description": "Request timeout in seconds. Clamped to 1-60.",
                "default": 30,
                "minimum": 1,
                "maximum": 60,
            },
            "max_bytes": {
                "type": "integer",
                "description": "Maximum response bytes to include. Clamped to 1-1000000.",
                "default": 100000,
                "minimum": 1,
                "maximum": 1000000,
            },
            "follow_redirects": {
                "type": "boolean",
                "description": "Follow HTTP redirects after validating each redirect target.",
                "default": False,
            },
            "max_redirects": {
                "type": "integer",
                "description": "Maximum redirects when follow_redirects is true. Clamped to 0-10.",
                "default": 5,
                "minimum": 0,
                "maximum": 10,
            },
        },
        "required": ["url"],
    },
}


def _handle_curl(args, **_kw):
    return curl_tool(
        url=args.get("url", ""),
        method=args.get("method", "GET"),
        headers=args.get("headers"),
        body=args.get("body"),
        timeout=args.get("timeout", 30),
        max_bytes=args.get("max_bytes", 100_000),
        follow_redirects=args.get("follow_redirects", False),
        max_redirects=args.get("max_redirects", 5),
    )


registry.register(
    name="curl",
    toolset="curl",
    schema=CURL_SCHEMA,
    handler=_handle_curl,
    emoji="🌐",
    max_result_size_chars=120_000,
)
