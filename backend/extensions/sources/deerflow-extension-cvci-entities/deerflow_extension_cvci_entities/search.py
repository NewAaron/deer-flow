"""A bounded UI search adapter. Business authorization remains in CVCI tools."""

import json
from collections.abc import Mapping
from uuid import UUID

from deerflow_extension_api import HostCapabilityError

NAMESPACE = "cvci-entities"
PAGE_SIZE = 12
PROVIDERS = {"VEHICLE": "vehicle", "SITE": "site", "DEVICE": "device", "DISPOSAL": "disposal", "CHECKPOINT": "checkpoint"}
KINDS = frozenset((*PROVIDERS, "PERMIT"))


def canonical_uuid(value):
    if not isinstance(value, str):
        return None
    try:
        parsed = str(UUID(value))
    except ValueError:
        return None
    return parsed if parsed == value.lower() else None


def _short_text(value, limit):
    if not isinstance(value, str):
        return ""
    # Match the browser's UTF-16 limits without splitting a surrogate pair.
    return value.encode("utf-16-le", errors="replace")[: limit * 2].decode("utf-16-le", errors="ignore").strip()


def _search_data(envelope):
    if not isinstance(envelope, Mapping):
        raise ValueError("CVCI search is unavailable")
    payload = envelope.get("structuredContent")
    if payload is None:
        blocks = envelope.get("content", [])
        if not isinstance(blocks, list):
            raise ValueError("CVCI search returned an invalid result")
        for block in blocks:
            if not isinstance(block, Mapping) or block.get("type") != "text" or not isinstance(block.get("text"), str):
                continue
            try:
                candidate = json.loads(block["text"])
            except (ValueError, TypeError):
                continue
            if isinstance(candidate, Mapping) and "status" in candidate:
                payload = candidate
                break
    if (
        envelope.get("isError") is True
        and isinstance(payload, Mapping)
        and type(payload.get("schemaVersion")) is int
        and payload["schemaVersion"] == 1
        and payload.get("status") == "ERROR"
        and isinstance(payload.get("error"), Mapping)
        and payload["error"].get("code") == "ACCESS_DENIED"
    ):
        # Only the explicit versioned CVCI refusal is a permission denial.
        # Never propagate its remote message or convert other failures to []/403.
        raise HostCapabilityError("mcp_read_access_denied")
    if envelope.get("isError"):
        raise ValueError("CVCI search is unavailable")
    if not isinstance(payload, Mapping) or payload.get("status") not in ("OK", "NO_RESULTS") or not isinstance(payload.get("data"), Mapping):
        raise ValueError("CVCI search returned an invalid result")
    return payload["data"]


async def search_subjects(payload, context):
    if context.settings.get("enabled") is not True or context.interaction != "session" or context.principal.is_internal or not context.principal.user_id:
        raise ValueError("Open archive search in an authenticated browser session")
    reader = getattr(context, "mcp_reader", None)
    if reader is None:
        raise ValueError("Session archive search is unavailable on this host")
    if not isinstance(payload, Mapping) or set(payload) - {"kind", "keyword", "keywordMode", "pageNo", "pageSize"}:
        raise ValueError("Invalid archive search input")
    kind = payload.get("kind")
    keyword = payload.get("keyword")
    mode = payload.get("keywordMode", None if kind == "VEHICLE" else "NAME")
    page = payload.get("pageNo", 1)
    if not isinstance(kind, str) or kind not in KINDS or not isinstance(keyword, str) or not keyword.strip() or len(keyword) > 160 or any(ord(char) < 32 for char in keyword):
        raise ValueError("Enter a supported archive type and keyword")
    if (kind == "VEHICLE" and mode is not None) or (kind != "VEHICLE" and mode not in ("NAME", "IDENTIFIER")):
        raise ValueError("Invalid archive search mode")
    page_size = payload.get("pageSize", PAGE_SIZE)
    if type(page) is not int or not 1 <= page <= 10000 or type(page_size) is not int or page_size != PAGE_SIZE:
        raise ValueError("Invalid archive search page")
    # CVCI's IDENTIFIER mode is exact; omitted mode preserves partial plate search.
    args = {"kind": kind, "keyword": keyword.strip(), "pageNo": page, "pageSize": PAGE_SIZE, **({"keywordMode": mode} if mode is not None else {})}
    data = _search_data(await reader.call("cvci", "cvci_search_archives", args))
    rows, total = data.get("items"), data.get("total")
    if not isinstance(rows, list) or len(rows) > PAGE_SIZE or type(total) is not int or total < len(rows):
        raise ValueError("CVCI search returned invalid pagination")
    items = []
    seen = set()
    for row in rows:
        if not isinstance(row, Mapping) or row.get("kind") != kind or (entity_id := canonical_uuid(row.get("id"))) is None:
            raise ValueError("CVCI search returned an invalid archive")
        permit_kind = row.get("permitKind") if kind == "PERMIT" else None
        if kind == "PERMIT" and permit_kind not in ("TRANSPORT", "DISPOSAL"):
            raise ValueError("CVCI search omitted the approval category")
        provider = f"permit-{permit_kind.lower()}" if kind == "PERMIT" else PROVIDERS[kind]
        key = provider, entity_id
        if key in seen:
            continue
        seen.add(key)
        code = _short_text(row.get("code"), 160)
        name = _short_text(row.get("name"), 120) or _short_text(code, 120) or "未命名档案"
        items.append(
            {
                "reference": {"namespace": NAMESPACE, "provider": provider, "id": entity_id, "label": name},
                "kind": kind,
                "name": name,
                "code": code or None,
                **({"permitKind": permit_kind} if permit_kind else {}),
            }
        )
    return {"items": items, "pageNo": page, "pageSize": PAGE_SIZE, "total": total, "hasMore": page * PAGE_SIZE < total}
