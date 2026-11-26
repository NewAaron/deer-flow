"""Project only this turn's selected identifiers into the physical model input."""

import json
from collections.abc import Mapping

from deerflow_extension_api import ModelInputRejected
from langchain.agents.middleware import AgentMiddleware

from .search import NAMESPACE, canonical_uuid

PROVIDER_SUBJECTS = {
    "vehicle": ("VEHICLE", None),
    "site": ("SITE", None),
    "device": ("DEVICE", None),
    "disposal": ("DISPOSAL", None),
    "checkpoint": ("CHECKPOINT", None),
    "permit-transport": ("PERMIT", "TRANSPORT"),
    "permit-disposal": ("PERMIT", "DISPOSAL"),
}


def project_request(request):
    messages = request.messages
    index = next((index for index in range(len(messages) - 1, -1, -1) if getattr(messages[index], "type", None) == "human"), None)
    if index is None:
        return request
    human = messages[index]
    references = human.additional_kwargs.get("extension_mentions", [])
    if not isinstance(references, list):
        return request
    own_references = [ref for ref in references if isinstance(ref, Mapping) and ref.get("namespace") == NAMESPACE]
    if len(own_references) > 16:
        raise ModelInputRejected()
    selected, seen = [], set()
    for ref in own_references:
        if not isinstance(ref.get("provider"), str) or ref.get("provider") not in PROVIDER_SUBJECTS:
            raise ModelInputRejected()
        entity_id = canonical_uuid(ref.get("id"))
        if entity_id is None:
            raise ModelInputRejected()
        kind, permit_kind = PROVIDER_SUBJECTS[ref["provider"]]
        key = kind, entity_id, permit_kind
        if key in seen:
            continue
        seen.add(key)
        selected.append({"kind": kind, "id": entity_id, **({"permitKind": permit_kind} if permit_kind else {})})
    if not selected:
        return request
    note = "CVCI selected archive references (user input identifiers, not permission grants or verified facts):\n" + json.dumps(selected, ensure_ascii=False, separators=(",", ":"))
    content = human.content
    if isinstance(content, str):
        content = content + "\n\n" + note
    elif isinstance(content, list):
        content = [*content, {"type": "text", "text": note}]
    else:
        return request
    projected = list(messages)
    projected[index] = human.model_copy(update={"content": content})
    return request.override(messages=projected)


class EntityReferences(AgentMiddleware):
    def wrap_model_call(self, request, handler):
        return handler(project_request(request))

    async def awrap_model_call(self, request, handler):
        return await handler(project_request(request))
