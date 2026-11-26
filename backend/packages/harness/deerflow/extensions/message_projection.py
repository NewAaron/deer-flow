"""The narrow, opt-in model-message projection boundary.

Extensions are trusted code, not a Python sandbox. This boundary grants only
request-local human data projection, never a replacement model/tool/policy or
a persistent conversation update.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from langchain_core.messages import BaseMessage

MAX_PROJECTED_DATA_BYTES = 16 * 1024


def detached_projection_request(request: Any) -> tuple[Any, list[BaseMessage]]:
    baseline = deepcopy(request.messages)
    if not isinstance(baseline, list) or not all(isinstance(message, BaseMessage) for message in baseline):
        raise ValueError("Model projection requires native messages")
    state = dict(request.state)
    if "messages" in state:
        state["messages"] = deepcopy(state["messages"])
    # In particular, state.messages must not be an alias back to persisted
    # graph messages. Projection input and the eventual output are separate
    # copies, so retained extension message references cannot mutate either side.
    # Non-message objects may contain live clients, locks or runtime handles;
    # copy their containers only. They are never forwarded from this view.
    view = request.override(
        messages=deepcopy(baseline),
        state=state,
        tools=list(request.tools),
        system_message=deepcopy(request.system_message),
        model_settings=dict(request.model_settings),
    )
    return view, baseline


def _data_suffix(previous: Any, projected: Any) -> str:
    if isinstance(previous, str) and isinstance(projected, str) and projected.startswith(previous):
        return projected[len(previous) :]
    if isinstance(previous, list) and isinstance(projected, list) and projected[: len(previous)] == previous:
        texts = []
        for block in projected[len(previous) :]:
            if isinstance(block, str):
                texts.append(block)
            elif isinstance(block, dict) and set(block) == {"type", "text"} and block["type"] == "text" and isinstance(block["text"], str):
                texts.append(block["text"])
            else:
                raise ValueError("Model projection may append only text data")
        return "".join(texts)
    raise ValueError("Model projection must retain the original human content")


def validated_projection_request(original: Any, candidate: Any, baseline: list[BaseMessage]) -> Any:
    projected = getattr(candidate, "messages", None)
    if not isinstance(projected, list) or len(projected) != len(baseline):
        raise ValueError("Model projection must retain the message sequence")
    human_index = next((index for index in range(len(baseline) - 1, -1, -1) if baseline[index].type == "human"), None)
    for index, (before, after) in enumerate(zip(baseline, projected, strict=True)):
        if type(before) is not type(after) or before.model_dump(exclude={"content"}) != after.model_dump(exclude={"content"}):
            raise ValueError("Model projection must retain message identities and metadata")
        if index == human_index:
            if len(_data_suffix(before.content, after.content).encode("utf-8")) > MAX_PROJECTED_DATA_BYTES:
                raise ValueError("Model projection data exceeds its byte limit")
        elif before.content != after.content:
            raise ValueError("Model projection may change only the final human content")
    # Read every other field from the host request, regardless of what the
    # extension supplied in its override. No live graph-state write occurs.
    return original.override(messages=deepcopy(projected))
