import assert from "node:assert/strict";
import test from "node:test";
import extension, { surfaceDefinitions, searchRequest, selectionText, normalizeSearchResult, appendCandidates, searchFailureMessage } from "../deerflow_extension_cvci_entities/static/index.mjs";

const id = "10000000-0000-4000-8000-000000000001";
const mode = (surface, key) => surfaceDefinitions.find((item) => item.id === surface).modes.find((item) => item.id === key);
const response = (kind, provider, extra = {}) => ({ items: [{ kind, name: "北门档案", code: "A-1", reference: { namespace: "cvci-entities", provider, id, label: "北门档案" }, ...extra }], pageNo: 1, pageSize: 12, total: 1, hasMore: false });

test("the four card surfaces are optional composer pickers", () => {
  assert.equal(extension.module, "cvci-entities.v1");
  assert.deepEqual(extension.surfaces.map(({ id, slot }) => [id, slot]), [
    ["vehicle-record", "composer-picker"], ["permit-query", "composer-picker"],
    ["project-info", "composer-picker"], ["device-record", "composer-picker"],
  ]);
});

test("permit and device modes select the subject, without relation or validity filters", () => {
  assert.deepEqual(searchRequest(mode("permit-query", "site"), " 北门 "), { kind: "SITE", keyword: "北门", keywordMode: "NAME", pageNo: 1, pageSize: 12 });
  assert.deepEqual(searchRequest(mode("permit-query", "vehicle"), "沪A12"), { kind: "VEHICLE", keyword: "沪A12", pageNo: 1, pageSize: 12 });
  const permit = mode("permit-query", "permit");
  assert.equal(searchRequest(permit, "核准").keywordMode, "NAME");
  assert.deepEqual(searchRequest(permit, "X-1", 2, { keywordMode: "IDENTIFIER" }), { kind: "PERMIT", keyword: "X-1", keywordMode: "IDENTIFIER", pageNo: 2, pageSize: 12 });
  const place = mode("device-record", "place");
  for (const kind of ["SITE", "DISPOSAL", "CHECKPOINT"]) {
    assert.equal(searchRequest(place, "北门", 1, { placeKind: kind }).kind, kind);
    assert.match(selectionText(place, kind), /当前安装的设备/);
  }
  assert.throws(() => searchRequest(place, "wrong", 1, { placeKind: "VEHICLE" }));
  assert.equal(searchRequest(mode("device-record", "device"), "PD-123").keywordMode, "IDENTIFIER");
  assert.equal(selectionText(mode("permit-query", "site")), "帮我查询这个工地关联的核准。");
  assert.equal(selectionText(mode("permit-query", "vehicle")), "帮我查询这辆车关联的核准。");
});

test("loading more keeps the chosen object and deduplicates by provider and ID", () => {
  const transport = response("PERMIT", "permit-transport", { permitKind: "TRANSPORT" }).items[0];
  const disposal = response("PERMIT", "permit-disposal", { permitKind: "DISPOSAL" }).items[0];
  const merged = appendCandidates([transport], [{ ...transport }, disposal]);
  assert.equal(merged.length, 2);
  assert.equal(merged[0], transport);
  assert.equal(merged[1], disposal);
});

test("actual permit category determines the reference provider; malformed IDs or mixed kinds fail", () => {
  const request = searchRequest(mode("permit-query", "permit"), "核准");
  for (const [permitKind, provider] of [["TRANSPORT", "permit-transport"], ["DISPOSAL", "permit-disposal"]]) {
    const valid = response("PERMIT", provider, { permitKind });
    assert.equal(normalizeSearchResult(valid, request).items[0].reference.provider, provider);
    assert.throws(() => normalizeSearchResult(response("PERMIT", "permit-transport", { permitKind: "DISPOSAL" }), request));
    valid.items[0].reference.id = "not-an-id";
    assert.throws(() => normalizeSearchResult(valid, request));
  }
  assert.throws(() => normalizeSearchResult(response("SITE", "site"), request));
  assert.throws(() => normalizeSearchResult({ ...response("PERMIT", "permit-transport", { permitKind: "TRANSPORT" }), hasMore: true }, request));
});

test("an archive access refusal names the required administrator action without suggesting sign-in", () => {
  const denied = { status: 403, code: "mcp_read_access_denied", message: "private upstream details" };
  const expected = "没有权限查询此类档案，请联系单位管理员配置相应业务权限。";
  assert.equal(searchFailureMessage(denied, true), expected);
  assert.equal(searchFailureMessage(denied, true, true), expected);
  assert.match(searchFailureMessage(denied, false), /permission.*administrator/i);
  assert.doesNotMatch(searchFailureMessage(denied, true), /登录|private|未找到/);
});

test("session, validation and transport failures retain their existing error messages", () => {
  const normal = "搜索暂时不可用，请重试；若持续失败，请重新登录。";
  const more = "更多档案暂时无法加载，已保留当前结果，请重试。";
  for (const failure of [
    { status: 403, code: "mcp_read_session_unavailable" },
    { status: 422, code: "mcp_read_access_denied" },
    { status: 503, code: "mcp_read_access_denied" },
    { status: 403 }, new Error("unexpected"), null,
  ]) {
    assert.equal(searchFailureMessage(failure, true), normal);
    assert.equal(searchFailureMessage(failure, true, true), more);
    assert.doesNotMatch(searchFailureMessage(failure, true), /未找到匹配/);
  }
});
