"""Feishu Document Tool -- read document content via Feishu/Lark API.

Provides ``feishu_doc_read`` for reading document content as plain text.
Uses the same lazy-import + BaseRequest pattern as feishu_comment.py.
"""

import json
import logging
import os
import threading

from tools.registry import registry, tool_error, tool_result

logger = logging.getLogger(__name__)

# Thread-local storage for the lark client injected by feishu_comment handler.
_local = threading.local()


def set_client(client):
    """Store a lark client for the current thread (called by feishu_comment)."""
    _local.client = client


def get_client():
    """Return the lark client for the current thread, or None."""
    return getattr(_local, "client", None)


def _build_fallback_client():
    """Build a generic Feishu/Lark client from env when not in comment context."""
    app_id = os.getenv("FEISHU_APP_ID", "").strip()
    app_secret = os.getenv("FEISHU_APP_SECRET", "").strip()
    domain_name = os.getenv("FEISHU_DOMAIN", "feishu").strip().lower() or "feishu"

    if not app_id or not app_secret:
        return None, "Feishu client not available (not in a Feishu comment context and FEISHU_APP_ID/FEISHU_APP_SECRET are missing)"

    try:
        import lark_oapi as lark
    except ImportError:
        return None, "lark_oapi not installed"

    domain = getattr(lark, "DOMAIN_FEISHU", None)
    if domain_name == "lark":
        domain = getattr(lark, "DOMAIN_LARK", domain)

    builder = (
        lark.Client.builder()
        .app_id(app_id)
        .app_secret(app_secret)
    )
    if domain is not None:
        builder = builder.domain(domain)
    if hasattr(lark, "LogLevel") and hasattr(lark.LogLevel, "WARNING"):
        builder = builder.log_level(lark.LogLevel.WARNING)
    return builder.build(), None


# ---------------------------------------------------------------------------
# feishu_doc_read
# ---------------------------------------------------------------------------

_RAW_CONTENT_URI = "/open-apis/docx/v1/documents/:document_id/raw_content"
_WIKI_GET_NODE_URI = "/open-apis/wiki/v2/spaces/get_node"

FEISHU_DOC_READ_SCHEMA = {
    "name": "feishu_doc_read",
    "description": (
        "Read the full content of a Feishu/Lark document as plain text. "
        "Useful when you need more context beyond the quoted text in a comment."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "doc_token": {
                "type": "string",
                "description": "The document token (from the document URL or comment context).",
            },
        },
        "required": ["doc_token"],
    },
}


def _check_feishu():
    try:
        import lark_oapi  # noqa: F401
        return True
    except ImportError:
        return False


def _parse_response_json(response) -> dict:
    raw = getattr(response, "raw", None)
    if raw and hasattr(raw, "content"):
        try:
            return json.loads(raw.content)
        except (json.JSONDecodeError, TypeError):
            return {}
    return {}


def _resolve_docx_token(client, token, access_token_type, http_method, base_request):
    """Resolve a wiki token to its underlying docx token when needed."""
    wiki_request = (
        base_request.builder()
        .http_method(http_method.GET)
        .uri(_WIKI_GET_NODE_URI)
        .token_types({access_token_type.TENANT})
        .queries([("token", token)])
        .build()
    )
    wiki_response = client.request(wiki_request)
    wiki_code = getattr(wiki_response, "code", None)
    if wiki_code != 0:
        wiki_msg = getattr(wiki_response, "msg", "unknown error")
        return None, tool_error(f"Failed to resolve wiki node: code={wiki_code} msg={wiki_msg}")

    wiki_body = _parse_response_json(wiki_response)
    node = wiki_body.get("data", {}).get("node", {})
    obj_type = node.get("obj_type", "")
    obj_token = node.get("obj_token", "")
    if not obj_type or not obj_token:
        return None, tool_error("Wiki node did not include obj_type/obj_token")
    if obj_type != "docx":
        return None, tool_error(f"Wiki node resolves to unsupported obj_type={obj_type}")
    return obj_token, None


def _read_raw_content(client, document_id, access_token_type, http_method, base_request):
    request = (
        base_request.builder()
        .http_method(http_method.GET)
        .uri(_RAW_CONTENT_URI)
        .token_types({access_token_type.TENANT})
        .paths({"document_id": document_id})
        .build()
    )
    return client.request(request)


def _extract_content_from_response(response):
    body = _parse_response_json(response)
    content = body.get("data", {}).get("content", "")
    if isinstance(content, str) and content:
        return content

    data = getattr(response, "data", None)
    if isinstance(data, dict):
        content = data.get("content", "")
        if isinstance(content, str) and content:
            return content
    elif isinstance(getattr(data, "content", None), str) and getattr(data, "content"):
        return getattr(data, "content")
    return ""


def _handle_feishu_doc_read(args: dict, **kwargs) -> str:
    doc_token = args.get("doc_token", "").strip()
    if not doc_token:
        return tool_error("doc_token is required")

    client = get_client()
    if client is None:
        client, error = _build_fallback_client()
        if client is None:
            return tool_error(error or "Feishu client not available")

    try:
        from lark_oapi import AccessTokenType
        from lark_oapi.core.enum import HttpMethod
        from lark_oapi.core.model.base_request import BaseRequest
    except ImportError:
        return tool_error("lark_oapi not installed")

    # If caller explicitly passed a wiki token, resolve it first.
    if doc_token.startswith("wiki"):
        effective_token, resolve_error = _resolve_docx_token(
            client, doc_token, AccessTokenType, HttpMethod, BaseRequest
        )
        if resolve_error:
            return resolve_error

        response = _read_raw_content(
            client, effective_token, AccessTokenType, HttpMethod, BaseRequest
        )
        code = getattr(response, "code", None)
        if code == 0:
            content = _extract_content_from_response(response)
            if content:
                return tool_result(success=True, content=content)
            return tool_error("No content returned from document API")
        msg = getattr(response, "msg", "unknown error")
        return tool_error(f"Failed to read document: code={code} msg={msg}")

    # First try: treat the token as a direct docx document id.
    response = _read_raw_content(client, doc_token, AccessTokenType, HttpMethod, BaseRequest)
    code = getattr(response, "code", None)
    if code == 0:
        content = _extract_content_from_response(response)
        if content:
            return tool_result(success=True, content=content)
        return tool_error("No content returned from document API")

    # Fallback: if the direct docx lookup misses, try resolving as a wiki node.
    if code == 1770002:
        effective_token, resolve_error = _resolve_docx_token(
            client, doc_token, AccessTokenType, HttpMethod, BaseRequest
        )
        if resolve_error:
            return resolve_error

        response = _read_raw_content(
            client, effective_token, AccessTokenType, HttpMethod, BaseRequest
        )
        code = getattr(response, "code", None)
        if code == 0:
            content = _extract_content_from_response(response)
            if content:
                return tool_result(success=True, content=content)
            return tool_error("No content returned from document API")

    msg = getattr(response, "msg", "unknown error")
    return tool_error(f"Failed to read document: code={code} msg={msg}")


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------

registry.register(
    name="feishu_doc_read",
    toolset="feishu_doc",
    schema=FEISHU_DOC_READ_SCHEMA,
    handler=_handle_feishu_doc_read,
    check_fn=_check_feishu,
    requires_env=[],
    is_async=False,
    description="Read Feishu document content",
    emoji="\U0001f4c4",
)
