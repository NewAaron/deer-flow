import json
from types import SimpleNamespace

import pytest
from deerflow_extension_api import HostCapabilityError

from deerflow_extension_cvci_entities.search import search_subjects

ENTITY = "10000000-0000-4000-8000-000000000001"


class Reader:
    def __init__(self, envelope):
        self.envelope = envelope
        self.calls = []

    async def call(self, server, tool, arguments):
        self.calls.append((server, tool, arguments))
        return self.envelope


def result(kind="DEVICE", **extra):
    item = {"id": ENTITY, "kind": kind, "name": "北门摄像头", "code": "CAM-1", **extra}
    return {"status": "OK", "data": {"items": [item], "total": 1}}


def context(reader, **extra):
    return SimpleNamespace(
        principal=SimpleNamespace(user_id="viewer", is_internal=False),
        settings={"enabled": True},
        interaction="session",
        mcp_reader=reader,
        **extra,
    )


@pytest.mark.asyncio
async def test_search_calls_only_the_existing_search_tool_without_relation_or_validity_filters():
    reader = Reader({"structuredContent": result()})
    actual = await search_subjects({"kind": "DEVICE", "keyword": "CAM", "keywordMode": "IDENTIFIER"}, context(reader))
    assert reader.calls == [("cvci", "cvci_search_archives", {"kind": "DEVICE", "keyword": "CAM", "keywordMode": "IDENTIFIER", "pageNo": 1, "pageSize": 12})]
    assert actual["items"][0]["reference"] == {"namespace": "cvci-entities", "provider": "device", "id": ENTITY, "label": "北门摄像头"}
    assert actual["total"] == 1 and actual["hasMore"] is False


@pytest.mark.asyncio
@pytest.mark.parametrize("kind,provider", [("VEHICLE", "vehicle"), ("SITE", "site"), ("DISPOSAL", "disposal"), ("CHECKPOINT", "checkpoint")])
async def test_subject_kind_and_provider_are_preserved(kind, provider):
    reader = Reader({"structuredContent": result(kind)})
    actual = await search_subjects({"kind": kind, "keyword": "北门"}, context(reader))
    assert actual["items"][0]["reference"]["provider"] == provider
    if kind == "VEHICLE":
        assert "keywordMode" not in reader.calls[0][2]
    else:
        assert reader.calls[0][2]["keywordMode"] == "NAME"


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", [{}, {"keywordMode": None}])
async def test_vehicle_plate_prefix_uses_existing_keyword_collection_search(mode):
    reader = Reader({"structuredContent": result("VEHICLE")})
    await search_subjects({"kind": "VEHICLE", "keyword": "沪A12", **mode}, context(reader))
    assert reader.calls == [("cvci", "cvci_search_archives", {"kind": "VEHICLE", "keyword": "沪A12", "pageNo": 1, "pageSize": 12})]


@pytest.mark.asyncio
@pytest.mark.parametrize("permit_kind,provider", [("TRANSPORT", "permit-transport"), ("DISPOSAL", "permit-disposal")])
async def test_permit_candidates_keep_their_actual_category(permit_kind, provider):
    reader = Reader({"structuredContent": result("PERMIT", permitKind=permit_kind)})
    actual = await search_subjects({"kind": "PERMIT", "keyword": "核准"}, context(reader))
    assert actual["items"][0]["reference"]["provider"] == provider
    assert "permitKind" not in reader.calls[0][2]
    assert "effectiveStatus" not in reader.calls[0][2]


@pytest.mark.asyncio
async def test_text_envelope_fallback_and_pagination_do_not_claim_all_results():
    data = result()
    data["data"]["total"] = 25
    reader = Reader({"content": [{"type": "text", "text": "CVCI result"}, {"type": "text", "text": json.dumps(data)}]})
    actual = await search_subjects({"kind": "DEVICE", "keyword": "摄像头", "pageNo": 2, "pageSize": 12}, context(reader))
    assert actual["pageNo"] == 2 and actual["hasMore"] is True


@pytest.mark.asyncio
async def test_empty_search_is_a_real_zero_result():
    reader = Reader({"structuredContent": {"status": "NO_RESULTS", "data": {"items": [], "total": 0}}})
    assert (await search_subjects({"kind": "SITE", "keyword": "无匹配"}, context(reader)))["items"] == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "change",
    [
        {"kind": "ENTERPRISE"},
        {"kind": []},
        {"kind": "DEVICE", "status": "ENABLED"},
        {"kind": "DEVICE", "keyword": ""},
        {"kind": "DEVICE", "pageNo": 0},
        {"kind": "DEVICE", "pageSize": 100},
        {"kind": "DEVICE", "pageSize": 12.0},
        {"kind": "DEVICE", "keywordMode": None},
        {"kind": "VEHICLE", "keywordMode": "NAME"},
        {"kind": "VEHICLE", "keywordMode": "IDENTIFIER"},
    ],
)
async def test_invalid_or_extra_fields_never_reach_mcp(change):
    reader = Reader({"structuredContent": result()})
    with pytest.raises(ValueError):
        await search_subjects({"kind": "DEVICE", "keyword": "camera", **change}, context(reader))
    assert reader.calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "value", [None, {}, {"content": None}, {"isError": True, "structuredContent": result()}, {"structuredContent": result("SITE")}, {"structuredContent": result("PERMIT")}, {"structuredContent": result(id="not-an-id")}]
)
async def test_malformed_upstream_is_an_error_not_an_empty_result(value):
    reader = Reader(value)
    with pytest.raises(ValueError):
        await search_subjects({"kind": "DEVICE", "keyword": "camera"}, context(reader))


@pytest.mark.asyncio
async def test_session_reader_is_required_and_never_replaced_with_a_static_identity():
    with pytest.raises(ValueError):
        await search_subjects({"kind": "DEVICE", "keyword": "camera"}, context(None))
    reader = Reader({"structuredContent": result()})
    non_session = context(reader)
    non_session.interaction = "other"
    with pytest.raises(ValueError):
        await search_subjects({"kind": "DEVICE", "keyword": "camera"}, non_session)
    assert reader.calls == []


def denied_payload():
    # Contract from McpConfiguration.invoke, not a captured production envelope.
    return {"schemaVersion": 1, "status": "ERROR", "error": {"code": "ACCESS_DENIED", "message": "private remote detail", "retryable": False}}


@pytest.mark.asyncio
@pytest.mark.parametrize("transport", ["structured", "text"])
async def test_explicit_mcp_access_denied_is_a_public_permission_error(transport):
    payload = denied_payload()
    envelope = {"isError": True, **({"structuredContent": payload} if transport == "structured" else {"content": [{"type": "text", "text": json.dumps(payload)}]})}
    with pytest.raises(HostCapabilityError) as error:
        await search_subjects({"kind": "SITE", "keyword": "test"}, context(Reader(envelope)))
    assert error.value.code == "mcp_read_access_denied"
    assert "private remote detail" not in str(error.value)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "envelope",
    [
        {"isError": True, "structuredContent": {**denied_payload(), "schemaVersion": True}},
        {"isError": True, "structuredContent": {**denied_payload(), "schemaVersion": 2}},
        {"isError": True, "structuredContent": {**denied_payload(), "status": "OK"}},
        {"isError": False, "structuredContent": denied_payload()},
        {"isError": "true", "structuredContent": denied_payload()},
        {"isError": True, "structuredContent": {**denied_payload(), "error": {"code": "INTERNAL_ERROR", "message": "ACCESS_DENIED"}}},
        {"isError": True, "content": [{"type": "text", "text": "ACCESS_DENIED"}]},
    ],
)
async def test_other_or_malformed_mcp_errors_are_not_reclassified_as_permission_denials(envelope):
    with pytest.raises(ValueError):
        await search_subjects({"kind": "SITE", "keyword": "test"}, context(Reader(envelope)))
