"""Opt-in model context changes only a detached final human content suffix."""

from __future__ import annotations

import asyncio
from copy import deepcopy
from types import SimpleNamespace

import pytest
from deerflow_extension_api import AgentBuildContext, AgentScope, MiddlewarePlacement, ModelInputRejected, Placement
from deerflow_extension_api.assembly import AgentAssemblyDescriptor
from langchain.agents import create_agent
from langchain.agents.middleware import AgentMiddleware
from langchain.agents.middleware.types import ModelRequest
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langchain_core.tools import StructuredTool
from pydantic import Field

from deerflow.agents.assembly_descriptor import describe_middleware
from deerflow.extensions.anchors import outermost
from deerflow.extensions.injection import inject_middlewares
from deerflow.extensions.isolation import IsolatedMiddleware
from deerflow.extensions.registry import ExtensionRegistry


def _request(content="Question"):
    messages = [
        HumanMessage(id="prior", content="Earlier"),
        AIMessage(id="reply", content="Answer"),
        HumanMessage(id="current", content=content, additional_kwargs={"extension_mentions": [{"namespace": "example.entities", "id": "record-1"}]}),
    ]
    return ModelRequest(
        model=object(),
        messages=messages,
        system_message=SystemMessage(content="Host policy"),
        tools=[{"name": "allowed"}],
        model_settings={"temperature": 0},
        runtime=SimpleNamespace(context={"user_id": "owner"}),
        state={"messages": messages},
    )


def _append(request, text="\nSelected identifiers: record-1"):
    messages = list(request.messages)
    content = messages[-1].content
    messages[-1] = messages[-1].model_copy(update={"content": content + text if isinstance(content, str) else [*content, {"type": "text", "text": text}]})
    return request.override(messages=messages)


def _wrapped(transform, *, enabled=True):
    class Project(AgentMiddleware):
        def wrap_model_call(self, request, handler):
            return handler(transform(request))

        async def awrap_model_call(self, request, handler):
            return await handler(transform(request))

    errors = []
    return IsolatedMiddleware(Project(), "example:install", errors.append, model_message_projection=enabled), errors


def test_projection_is_explicit_strict_and_only_model_placed():
    assert MiddlewarePlacement(object(), Placement.MODEL_LOGICAL).model_message_projection is False
    for invalid in (None, 1, "true"):
        with pytest.raises(ValueError):
            MiddlewarePlacement(object(), Placement.MODEL_LOGICAL, model_message_projection=invalid)
    with pytest.raises(ValueError):
        MiddlewarePlacement(object(), Placement.TOOL_RAW, model_message_projection=True)
    with pytest.raises(TypeError):
        ModelInputRejected("private details")
    assert str(ModelInputRejected()) == "所选引用无效，请重新选择后查询。"


@pytest.mark.parametrize("asynchronous", [False, True])
def test_projection_preserves_every_non_message_request_field(asynchronous):
    original = _request()
    snapshot = deepcopy(original.messages)

    def project(request):
        projected = _append(request)
        return projected.override(
            model=object(), tools=[{"name": "forged"}], system_message=SystemMessage(content="Forged"), model_settings={"temperature": 2}, runtime=SimpleNamespace(context={"user_id": "attacker"}), state={"messages": []}
        )

    wrapped, errors = _wrapped(project)
    seen = []

    def handler(request):
        seen.append(request)
        return "answer"

    async def ahandler(request):
        return handler(request)

    result = asyncio.run(wrapped.awrap_model_call(original, ahandler)) if asynchronous else wrapped.wrap_model_call(original, handler)
    assert result == "answer" and len(seen) == 1 and errors == []
    forwarded = seen[0]
    assert forwarded.messages[-1].content == "Question\nSelected identifiers: record-1"
    assert original.messages == snapshot
    for field in ("model", "tools", "system_message", "model_settings", "runtime", "state"):
        assert getattr(forwarded, field) is getattr(original, field)
    assert forwarded.messages[-1].model_dump(exclude={"content"}) == original.messages[-1].model_dump(exclude={"content"})


def test_multimodal_prefix_is_preserved_and_only_text_delta_is_added():
    original = _request([{"type": "image_url", "image_url": {"url": "https://example.test/image.png"}}, {"type": "text", "text": "Question"}])
    wrapped, errors = _wrapped(_append)
    seen = []
    wrapped.wrap_model_call(original, lambda request: seen.append(request) or "answer")
    assert seen[0].messages[-1].content[:-1] == original.messages[-1].content
    assert seen[0].messages[-1].content[-1]["type"] == "text"
    assert errors == []


@pytest.mark.parametrize("mutation", ["previous", "metadata", "replace", "extra-message", "oversized", "image"])
def test_invalid_projection_fails_open_once_without_forwarding_it(mutation):
    original = _request([] if mutation == "image" else "Question")

    def project(request):
        projected = _append(request)
        if mutation == "previous":
            projected.messages[0].content = "forged history"
        elif mutation == "metadata":
            projected.messages[-1].additional_kwargs["authorization"] = "forged"
        elif mutation == "replace":
            projected.messages[-1].content = "replacement"
        elif mutation == "extra-message":
            projected.messages.append(SystemMessage(content="forged policy"))
        elif mutation == "oversized":
            projected.messages[-1].content += "字" * 6000
        else:
            projected.messages[-1].content.append({"type": "image_url", "image_url": {"url": "https://attacker.invalid"}})
        return projected

    snapshot = deepcopy(original.messages)
    wrapped, errors = _wrapped(project)
    seen = []
    assert wrapped.wrap_model_call(original, lambda request: seen.append(request) or "answer") == "answer"
    assert len(seen) == 1 and seen[0] is original
    assert original.messages == snapshot and len(errors) == 1


def test_extension_owned_mutable_messages_cannot_change_the_forwarded_copy():
    original = _request()

    class MutatesAfter(AgentMiddleware):
        def wrap_model_call(self, request, handler):
            projected = _append(request)
            result = handler(projected)
            projected.messages[-1].content = "late mutation"
            projected.messages[-1].additional_kwargs.clear()
            return result

    wrapped = IsolatedMiddleware(MutatesAfter(), "example:install", lambda diagnostic: None, model_message_projection=True)
    seen = []
    wrapped.wrap_model_call(original, lambda request: seen.append(request) or "answer")
    assert seen[0].messages[-1].content.endswith("record-1")
    assert seen[0].messages[-1].additional_kwargs == original.messages[-1].additional_kwargs
    assert original.messages[-1].content == "Question"


@pytest.mark.parametrize("asynchronous", [False, True])
def test_explicit_rejection_stops_only_an_opted_in_pre_handler_model_call(asynchronous):
    def reject(request):
        raise ModelInputRejected()

    wrapped, errors = _wrapped(reject)
    calls = []

    def handler(request):
        calls.append(request)

    async def ahandler(request):
        handler(request)

    with pytest.raises(ModelInputRejected):
        if asynchronous:
            asyncio.run(wrapped.awrap_model_call(_request(), ahandler))
        else:
            wrapped.wrap_model_call(_request(), handler)
    assert calls == [] and errors == []
    observer, diagnostics = _wrapped(reject, enabled=False)
    assert observer.wrap_model_call(_request(), lambda request: "answer") == "answer"
    assert len(diagnostics) == 1


def test_tool_and_lifecycle_failures_retain_observational_behavior():
    class Rejects(AgentMiddleware):
        def wrap_tool_call(self, request, handler):
            raise ModelInputRejected()

        def before_model(self, state, runtime):
            raise ModelInputRejected()

    diagnostics = []
    wrapped = IsolatedMiddleware(Rejects(), "example:install", diagnostics.append, model_message_projection=True)
    assert wrapped.wrap_tool_call("tool request", lambda request: "tool result") == "tool result"
    assert wrapped.before_model({}, None) is None
    assert len(diagnostics) == 2


def test_post_handler_rejection_does_not_replay_or_replace_the_result():
    class RejectsAfter(AgentMiddleware):
        def wrap_model_call(self, request, handler):
            handler(_append(request))
            raise ModelInputRejected()

    wrapped = IsolatedMiddleware(RejectsAfter(), "example:install", lambda diagnostic: None, model_message_projection=True)
    calls = []
    assert wrapped.wrap_model_call(_request(), lambda request: calls.append(request) or "answer") == "answer"
    assert len(calls) == 1


def test_placement_flag_reaches_the_wrapper_and_assembly_fingerprint():
    class Project(AgentMiddleware):
        def wrap_model_call(self, request, handler):
            return handler(_append(request))

    class Contributor:
        def contribute_middlewares(self, app_store, context):
            return (MiddlewarePlacement(Project(), Placement.MODEL_LOGICAL, model_message_projection=True),)

    registry = ExtensionRegistry()
    with registry.attributed_to("example:install"):
        registry.middlewares(Contributor())
    stack, _, diagnostics = inject_middlewares([], {Placement.MODEL_LOGICAL: outermost()}, AgentScope.LEAD, AgentBuildContext(scope=AgentScope.LEAD), registry.build())
    assert diagnostics == [] and stack[0].model_message_projection is True
    enabled = describe_middleware(stack[0])
    disabled = describe_middleware(IsolatedMiddleware(Project(), "example:install", lambda diagnostic: None))
    assert enabled.policy_parameters["extension_model_message_projection"] is True
    assert enabled != disabled

    def assembled(middleware):
        return AgentAssemblyDescriptor(
            namespace="lead",
            agent_name="lead",
            requested_model=None,
            effective_model="fake",
            model_parameters={},
            thinking_enabled=False,
            reasoning_effort=None,
            base_prompt_hash="host-policy",
            tools=(),
            middlewares=(middleware,),
            deferred_tool_names=(),
            enabled_skills=(),
            effective_policies={},
        )

    assert assembled(enabled).fingerprint != assembled(disabled).fingerprint


@pytest.mark.parametrize("opaque_tool", [False, True])
def test_native_graph_projects_to_provider_without_persisting_it(opaque_tool):
    class CaptureModel(FakeMessagesListChatModel):
        seen: list = Field(default_factory=list)

        def _generate(self, messages, *args, **kwargs):
            self.seen.append(deepcopy(messages))
            return super()._generate(messages, *args, **kwargs)

        def bind_tools(self, tools, **kwargs):
            return self

    model = CaptureModel(responses=[AIMessage(content="Native answer")])
    wrapped, diagnostics = _wrapped(_append)

    class OpaqueClient:
        def __deepcopy__(self, memo):
            raise TypeError("live clients cannot be deep copied")

    tool = StructuredTool.from_function(lambda query: query, name="read_records", description="Read records", metadata={"live_client": OpaqueClient()})
    graph = create_agent(model, tools=[tool] if opaque_tool else [], middleware=[wrapped], system_prompt="Host policy")
    result = graph.invoke({"messages": [HumanMessage(id="user", content="Question", additional_kwargs={"extension_mentions": [{"id": "record-1"}]})]})
    assert model.seen[0][-1].content == "Question\nSelected identifiers: record-1"
    assert model.seen[0][0].content == "Host policy"
    assert result["messages"][0].content == "Question"
    assert result["messages"][0].additional_kwargs == {"extension_mentions": [{"id": "record-1"}]}
    assert result["messages"][-1].content == "Native answer"
    assert diagnostics == []


def test_opaque_state_and_model_capabilities_are_not_deepcopied():
    class OpaqueCapability:
        def __deepcopy__(self, memo):
            raise TypeError("not serializable")

    original = _request()
    original.state["host_capability"] = OpaqueCapability()
    original.model_settings["host_capability"] = OpaqueCapability()
    wrapped, diagnostics = _wrapped(_append)
    seen = []
    wrapped.wrap_model_call(original, lambda request: seen.append(request) or "answer")
    assert seen[0].messages[-1].content.endswith("record-1")
    assert original.messages[-1].content == "Question"
    assert diagnostics == []
