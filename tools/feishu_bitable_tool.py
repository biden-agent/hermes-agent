"""Feishu Bitable Tool -- read table records via Feishu/Lark API.

Provides ``feishu_bitable_read`` for reading records from a Feishu/Lark
bitable (multi-dimensional table) by wiki/doc token + table/view ids.
Uses the same lazy-import + BaseRequest pattern as feishu_doc_tool.
"""

import json
import logging
import os
import threading

from tools.registry import registry, tool_error, tool_result

logger = logging.getLogger(__name__)

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
        return None, (
            "Feishu client not available (not in a Feishu comment context and "
            "FEISHU_APP_ID/FEISHU_APP_SECRET are missing)"
        )

    try:
        import lark_oapi as lark
    except ImportError:
        return None, "lark_oapi not installed"

    domain = getattr(lark, "DOMAIN_FEISHU", None)
    if domain_name == "lark":
        domain = getattr(lark, "DOMAIN_LARK", domain)

    builder = lark.Client.builder().app_id(app_id).app_secret(app_secret)
    if domain is not None:
        builder = builder.domain(domain)
    if hasattr(lark, "LogLevel") and hasattr(lark.LogLevel, "WARNING"):
        builder = builder.log_level(lark.LogLevel.WARNING)
    return builder.build(), None


_WIKI_GET_NODE_URI = "/open-apis/wiki/v2/spaces/get_node"
_BITABLE_RECORDS_URI = "/open-apis/bitable/v1/apps/:app_token/tables/:table_id/records"

FEISHU_BITABLE_READ_SCHEMA = {
    "name": "feishu_bitable_read",
    "description": (
        "Read records from a Feishu/Lark bitable (multi-dimensional table). "
        "Accepts a wiki/doc token that resolves to a bitable app plus table/view ids."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "doc_token": {
                "type": "string",
                "description": "Wiki/bitable token from the Feishu/Lark URL.",
            },
            "table_id": {
                "type": "string",
                "description": "The target bitable table ID.",
            },
            "view_id": {
                "type": "string",
                "description": "Optional bitable view ID to filter records.",
            },
            "page_size": {
                "type": "integer",
                "description": "Number of records to return (default 100, max 500).",
                "default": 100,
            },
            "page_token": {
                "type": "string",
                "description": "Pagination token for the next page.",
            },
        },
        "required": ["doc_token", "table_id"],
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


def _resolve_bitable_app_token(client, token, access_token_type, http_method, base_request):
    request = (
        base_request.builder()
        .http_method(http_method.GET)
        .uri(_WIKI_GET_NODE_URI)
        .token_types({access_token_type.TENANT})
        .queries([("token", token)])
        .build()
    )
    response = client.request(request)
    code = getattr(response, "code", None)
    if code != 0:
        msg = getattr(response, "msg", "unknown error")
        return None, None, tool_error(f"Failed to resolve wiki node: code={code} msg={msg}")

    body = _parse_response_json(response)
    node = body.get("data", {}).get("node", {})
    obj_type = node.get("obj_type", "")
    obj_token = node.get("obj_token", "")
    title = node.get("title", "")
    if not obj_type or not obj_token:
        return None, None, tool_error("Wiki node did not include obj_type/obj_token")
    if obj_type != "bitable":
        return None, None, tool_error(f"Resolved object is not a bitable: obj_type={obj_type}")
    return obj_token, title, None


def _read_records(client, app_token, table_id, view_id, page_size, page_token, access_token_type, http_method, base_request):
    queries = [("page_size", str(page_size))]
    if view_id:
        queries.append(("view_id", view_id))
    if page_token:
        queries.append(("page_token", page_token))

    request = (
        base_request.builder()
        .http_method(http_method.GET)
        .uri(_BITABLE_RECORDS_URI)
        .token_types({access_token_type.TENANT})
        .paths({"app_token": app_token, "table_id": table_id})
        .queries(queries)
        .build()
    )
    return client.request(request)


def _extract_records_payload(response) -> dict:
    body = _parse_response_json(response)
    data = body.get("data", {})
    if isinstance(data, dict) and data:
        return data

    response_data = getattr(response, "data", None)
    if isinstance(response_data, dict):
        return response_data
    if response_data and hasattr(response_data, "__dict__"):
        return vars(response_data)
    return {}


def _handle_feishu_bitable_read(args: dict, **kwargs) -> str:
    doc_token = args.get("doc_token", "").strip()
    table_id = args.get("table_id", "").strip()
    view_id = args.get("view_id", "").strip()
    page_token = args.get("page_token", "").strip()
    page_size = args.get("page_size", 100)

    if not doc_token:
        return tool_error("doc_token is required")
    if not table_id:
        return tool_error("table_id is required")

    try:
        page_size = int(page_size)
    except (TypeError, ValueError):
        return tool_error("page_size must be an integer")
    page_size = max(1, min(page_size, 500))

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

    app_token, title, resolve_error = _resolve_bitable_app_token(
        client, doc_token, AccessTokenType, HttpMethod, BaseRequest
    )
    if resolve_error:
        return resolve_error

    response = _read_records(
        client,
        app_token,
        table_id,
        view_id,
        page_size,
        page_token,
        AccessTokenType,
        HttpMethod,
        BaseRequest,
    )
    code = getattr(response, "code", None)
    if code != 0:
        msg = getattr(response, "msg", "unknown error")
        return tool_error(f"Failed to read bitable records: code={code} msg={msg}")

    data = _extract_records_payload(response)
    return tool_result(
        success=True,
        title=title,
        app_token=app_token,
        table_id=table_id,
        view_id=view_id or None,
        items=data.get("items", []),
        has_more=bool(data.get("has_more", False)),
        page_token=data.get("page_token", ""),
        total=data.get("total"),
    )


registry.register(
    name="feishu_bitable_read",
    toolset="feishu_bitable",
    schema=FEISHU_BITABLE_READ_SCHEMA,
    handler=_handle_feishu_bitable_read,
    check_fn=_check_feishu,
    requires_env=[],
    is_async=False,
    description="Read Feishu/Lark bitable records",
    emoji="📊",
)
