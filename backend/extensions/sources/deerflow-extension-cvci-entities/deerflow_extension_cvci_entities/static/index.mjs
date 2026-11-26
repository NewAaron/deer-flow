const NAMESPACE = "cvci-entities";
const PAGE_SIZE = 12;
const PROVIDERS = Object.freeze({
  VEHICLE: "vehicle", SITE: "site", DEVICE: "device", DISPOSAL: "disposal", CHECKPOINT: "checkpoint",
});
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;

export const surfaceDefinitions = Object.freeze([
  { id: "vehicle-record", title: "车辆档案", titleEn: "Vehicle records", modes: [
    { id: "vehicle", label: "按车辆", labelEn: "Vehicle", kind: "VEHICLE", text: "帮我查询这辆车的档案及关联信息。" },
  ] },
  { id: "permit-query", title: "核准内容", titleEn: "Approvals", modes: [
    { id: "site", label: "按工地", labelEn: "Site", kind: "SITE", mode: "NAME", text: "帮我查询这个工地关联的核准。" },
    { id: "vehicle", label: "按车辆", labelEn: "Vehicle", kind: "VEHICLE", text: "帮我查询这辆车关联的核准。" },
    { id: "permit", label: "按核准", labelEn: "Approval", kind: "PERMIT", mode: "NAME", text: "帮我查询这个核准的档案信息。", chooseMode: true },
  ] },
  { id: "project-info", title: "项目资料", titleEn: "Project information", modes: [
    { id: "site", label: "按工地", labelEn: "Site", kind: "SITE", mode: "NAME", text: "帮我查询这个项目的资料及参与主体。" },
  ] },
  { id: "device-record", title: "设备档案", titleEn: "Device records", modes: [
    { id: "device", label: "按设备编号", labelEn: "Device number", kind: "DEVICE", mode: "IDENTIFIER", text: "帮我查询这个设备的档案及当前安装关系。" },
    { id: "vehicle", label: "按车辆", labelEn: "Vehicle", kind: "VEHICLE", text: "帮我查询这辆车当前安装的设备。" },
    { id: "place", label: "按场地", labelEn: "Location", kind: "SITE", mode: "NAME", text: "帮我查询这个工地当前安装的设备。", choosePlace: true },
  ] },
]);

export function searchRequest(mode, keyword, pageNo = 1, options = {}) {
  const kind = mode.choosePlace ? options.placeKind ?? "SITE" : mode.kind;
  if (!Object.hasOwn(PROVIDERS, kind) && kind !== "PERMIT") throw new Error("Unsupported archive type");
  if (mode.choosePlace && !["SITE", "DISPOSAL", "CHECKPOINT"].includes(kind)) throw new Error("Unsupported place type");
  const keywordMode = mode.chooseMode ? options.keywordMode ?? "NAME" : mode.mode;
  if (kind !== "VEHICLE" && !["NAME", "IDENTIFIER"].includes(keywordMode)) throw new Error("Unsupported search mode");
  return { kind, keyword: keyword.trim(), ...(kind === "VEHICLE" ? {} : { keywordMode }), pageNo, pageSize: PAGE_SIZE };
}

export function appendCandidates(current, incoming) {
  const seen = new Set(current.map(({ reference }) => `${reference.provider}:${reference.id}`));
  return [...current, ...incoming.filter(({ reference }) => {
    const key = `${reference.provider}:${reference.id}`;
    if (seen.has(key)) return false;
    seen.add(key);
    return true;
  })];
}

export function selectionText(mode, placeKind = "SITE") {
  if (!mode.choosePlace) return mode.text;
  return { SITE: "帮我查询这个工地当前安装的设备。", DISPOSAL: "帮我查询这个消纳场当前安装的设备。", CHECKPOINT: "帮我查询这个卡口当前安装的设备。" }[placeKind];
}

export function searchFailureMessage(error, chinese = true, append = false) {
  if (error?.status === 403 && error?.code === "mcp_read_access_denied") {
    return chinese ? "没有权限查询此类档案，请联系单位管理员配置相应业务权限。"
      : "You do not have permission to query this type of record. Contact your organization administrator to configure the required business permissions.";
  }
  if (append) return chinese ? "更多档案暂时无法加载，已保留当前结果，请重试。"
    : "More records could not be loaded. Current results are kept; please retry.";
  return chinese ? "搜索暂时不可用，请重试；若持续失败，请重新登录。"
    : "Search is unavailable. Retry, or sign in again if the problem persists.";
}

export function normalizeSearchResult(result, request) {
  if (!result || !Array.isArray(result.items) || result.items.length > PAGE_SIZE ||
      !Number.isInteger(result.total) || result.total < result.items.length ||
      result.pageNo !== request.pageNo || result.pageSize !== PAGE_SIZE ||
      typeof result.hasMore !== "boolean" || result.hasMore !== (request.pageNo * PAGE_SIZE < result.total)) {
    throw new Error("Invalid archive search response");
  }
  const seen = new Set();
  const items = result.items.map((item) => {
    const ref = item?.reference;
    const provider = request.kind === "PERMIT" ? { TRANSPORT: "permit-transport", DISPOSAL: "permit-disposal" }[item?.permitKind] : PROVIDERS[request.kind];
    if (!provider || item.kind !== request.kind || ref?.namespace !== NAMESPACE || ref.provider !== provider ||
        typeof ref.id !== "string" || !UUID.test(ref.id) || typeof ref.label !== "string" || !ref.label.trim() || ref.label.length > 120 ||
        typeof item.name !== "string" || !item.name.trim() || item.name.length > 120 ||
        (item.code != null && (typeof item.code !== "string" || item.code.length > 160))) {
      throw new Error("Invalid archive candidate");
    }
    const key = `${ref.provider}:${ref.id}`;
    if (seen.has(key)) throw new Error("Duplicate archive candidate");
    seen.add(key);
    return { ...item, reference: { namespace: NAMESPACE, provider: ref.provider, id: ref.id, label: ref.label } };
  });
  return { ...result, items };
}

function mountPicker(root, context, definition) {
  const zh = context.locale?.toLowerCase().startsWith("zh") ?? true;
  const t = (chinese, english) => zh ? chinese : english;
  const sheet = document.createElement("link");
  sheet.rel = "stylesheet";
  sheet.href = new URL("./style.css", import.meta.url).href;
  const container = document.createElement("section");
  container.className = "cvci-picker";
  container.dataset.testid = "cvci-picker";
  container.setAttribute("aria-label", zh ? definition.title : definition.titleEn);
  const el = (tag, className, text) => {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined) node.textContent = text;
    return node;
  };
  const button = (text, className = "cvci-button") => {
    const node = el("button", className, text);
    node.type = "button";
    return node;
  };
  container.append(el("p", "cvci-intro", t("选择一个档案，交由助手继续查询。", "Select a record for the assistant to query.")));
  const tabs = el("div", "cvci-tabs");
  tabs.setAttribute("role", "tablist");
  tabs.setAttribute("aria-label", t("查询对象", "Query subject"));
  if (definition.modes.length > 1) container.append(tabs);
  const controls = el("div", "cvci-controls");
  const optionLabel = el("label", "cvci-option-label");
  const optionCaption = el("span");
  const optionSelect = el("select", "cvci-select");
  optionLabel.append(optionCaption, optionSelect);
  const searchLabel = el("label", "cvci-search-label");
  const searchCaption = el("span");
  const input = el("input", "cvci-input");
  input.type = "search";
  input.maxLength = 160;
  input.autocomplete = "off";
  input.dataset.testid = "cvci-search-input";
  searchLabel.append(searchCaption, input);
  controls.append(optionLabel, searchLabel);
  const status = el("p", "cvci-status");
  status.dataset.testid = "cvci-status";
  status.setAttribute("role", "status");
  status.setAttribute("aria-live", "polite");
  const retry = button(t("重试", "Retry"), "cvci-button cvci-retry");
  retry.hidden = true;
  const results = el("div", "cvci-results");
  results.setAttribute("role", "radiogroup");
  results.setAttribute("aria-label", t("档案搜索结果", "Archive search results"));
  const pagination = el("div", "cvci-pagination");
  const pageLabel = el("span", "cvci-page-label");
  const next = button(t("加载更多", "Load more"));
  next.dataset.testid = "cvci-next";
  pagination.append(pageLabel, next);
  pagination.hidden = true;
  const footer = el("div", "cvci-footer");
  const confirm = button(t("查询", "Query"), "cvci-button cvci-confirm");
  confirm.dataset.testid = "cvci-confirm";
  confirm.disabled = true;
  footer.append(el("span", "cvci-hint", t("单选后查询", "Choose one record")), confirm);
  container.append(controls, status, retry, results, pagination, footer);
  root.append(sheet, container);

  let mode = definition.modes[0];
  let placeKind = "SITE";
  let keywordMode = "NAME";
  let page = 1;
  let hasMore = false;
  let items = [];
  let selected = null;
  let loading = false;
  let submitting = false;
  let composing = false;
  let disposed = false;
  let generation = 0;
  let timer;
  let lastRequest;
  const alive = (version = generation) => !disposed && !context.signal?.aborted && version === generation;
  function updateConfirm() {
    confirm.disabled = !selected || loading || submitting || typeof context.confirmSelection !== "function";
  }
  function reset() {
    generation++;
    clearTimeout(timer);
    selected = null;
    items = [];
    page = 0;
    hasMore = false;
    results.replaceChildren();
    pagination.hidden = true;
    retry.hidden = true;
    loading = false;
    container.removeAttribute("aria-busy");
    status.textContent = typeof context.confirmSelection === "function"
      ? t("输入关键词搜索档案。", "Enter a keyword to find records.")
      : t("当前宿主暂不支持档案选择，请更新后重试。", "This host does not support record selection yet.");
    updateConfirm();
  }
  function choose(item, focus = false) {
    if (loading || submitting) return;
    selected = item;
    Array.from(results.children).forEach((node, index) => {
      const checked = items[index] === item;
      node.setAttribute("aria-checked", String(checked));
      node.tabIndex = checked ? 0 : -1;
      if (checked && focus) node.focus();
    });
    updateConfirm();
  }
  function paintResults() {
    const scrollTop = results.scrollTop;
    results.replaceChildren();
    items.forEach((item, index) => {
      const row = button(undefined, "cvci-result");
      row.dataset.testid = "cvci-result";
      row.setAttribute("role", "radio");
      row.setAttribute("aria-checked", String(selected === item));
      row.tabIndex = selected ? (selected === item ? 0 : -1) : (index === 0 ? 0 : -1);
      row.append(el("span", "cvci-result-name", item.name));
      const metadata = [item.code];
      if (item.permitKind) metadata.push(item.permitKind === "TRANSPORT" ? t("运输核准", "Transport approval") : t("消纳核准", "Disposal approval"));
      if (metadata.some(Boolean)) row.append(el("span", "cvci-result-code", metadata.filter(Boolean).join(" · ")));
      row.addEventListener("click", () => choose(item));
      row.addEventListener("keydown", (event) => {
        if (!["ArrowDown", "ArrowUp", "ArrowRight", "ArrowLeft", "Home", "End"].includes(event.key)) return;
        event.preventDefault();
        const target = event.key === "Home" ? 0 : event.key === "End" ? items.length - 1
          : (index + (["ArrowDown", "ArrowRight"].includes(event.key) ? 1 : -1) + items.length) % items.length;
        choose(items[target], true);
      });
      results.append(row);
    });
    results.scrollTop = scrollTop;
  }
  async function search(targetPage = 1) {
    if (!alive() || composing || submitting) return;
    clearTimeout(timer);
    const keyword = input.value.trim();
    if (!keyword) { reset(); return; }
    const append = targetPage > 1;
    if (append && targetPage !== page + 1) return;
    const version = ++generation;
    if (!append) {
      selected = null;
      items = [];
      page = 0;
      hasMore = false;
      results.replaceChildren();
      pagination.hidden = true;
    }
    retry.hidden = true;
    loading = true;
    container.setAttribute("aria-busy", "true");
    status.textContent = append ? t("正在加载更多…", "Loading more…") : t("正在搜索…", "Searching…");
    next.disabled = true;
    next.textContent = t("正在加载…", "Loading…");
    updateConfirm();
    const request = searchRequest(mode, keyword, targetPage, { placeKind, keywordMode });
    lastRequest = request;
    try {
      const response = normalizeSearchResult(await context.callBackend("search_subjects", request), request);
      if (!alive(version)) return;
      items = append ? appendCandidates(items, response.items) : response.items;
      page = targetPage;
      hasMore = response.hasMore;
      paintResults();
      status.textContent = items.length ? t(`找到 ${response.total} 条档案，请选择一条。`, `${response.total} records found. Choose one.`)
        : t("未找到匹配档案，请尝试其他关键词。", "No matching records. Try another keyword.");
      next.textContent = t("加载更多", "Load more");
      pagination.hidden = !hasMore;
      pageLabel.textContent = t(`已加载 ${items.length} 条`, `${items.length} loaded`);
    } catch (error) {
      if (!alive(version)) return;
      status.textContent = searchFailureMessage(error, zh, append);
      if (append) next.textContent = t("重试加载更多", "Retry loading more");
      else retry.hidden = false;
    } finally {
      if (alive(version)) {
        loading = false;
        container.removeAttribute("aria-busy");
        next.disabled = !hasMore;
        updateConfirm();
      }
    }
  }
  function schedule() {
    reset();
    page = 1;
    if (!composing && input.value.trim()) timer = setTimeout(() => { void search(); }, 250);
  }
  function renderControls() {
    optionSelect.replaceChildren();
    optionLabel.hidden = !mode.chooseMode && !mode.choosePlace;
    const options = mode.choosePlace ? [["SITE", "工地", "Site"], ["DISPOSAL", "消纳场", "Disposal site"], ["CHECKPOINT", "卡口", "Checkpoint"]]
      : [["NAME", "名称", "Name"], ["IDENTIFIER", "编号", "Number"]];
    for (const [value, label, english] of options) {
      const option = el("option", undefined, t(label, english));
      option.value = value;
      optionSelect.append(option);
    }
    optionSelect.value = mode.choosePlace ? placeKind : keywordMode;
    optionCaption.textContent = mode.choosePlace ? t("场地类型", "Location type") : t("搜索方式", "Search by");
    const kind = mode.choosePlace ? placeKind : mode.kind;
    const actualMode = mode.chooseMode ? keywordMode : mode.mode;
    const label = kind === "VEHICLE" ? t("车牌号", "License plate") : actualMode === "IDENTIFIER" ? t("档案编号", "Record number") : t("档案名称", "Record name");
    searchCaption.textContent = label;
    input.placeholder = t(`请输入${label}`, `Enter ${label.toLowerCase()}`);
  }
  const tabButtons = definition.modes.map((entry, index) => {
    const tab = button(t(entry.label, entry.labelEn), "cvci-tab");
    tab.setAttribute("role", "tab");
    tab.setAttribute("aria-selected", String(index === 0));
    tab.tabIndex = index === 0 ? 0 : -1;
    function activate() {
      if (submitting) return;
      mode = entry;
      keywordMode = "NAME";
      placeKind = "SITE";
      input.value = "";
      composing = false;
      reset();
      renderControls();
      tabButtons.forEach((node) => {
        node.setAttribute("aria-selected", String(node === tab));
        node.tabIndex = node === tab ? 0 : -1;
      });
      input.focus({ preventScroll: true });
    }
    tab.addEventListener("click", activate);
    tab.addEventListener("keydown", (event) => {
      if (!["ArrowRight", "ArrowLeft", "Home", "End"].includes(event.key)) return;
      event.preventDefault();
      const target = event.key === "Home" ? 0 : event.key === "End" ? definition.modes.length - 1
        : (index + (event.key === "ArrowRight" ? 1 : -1) + definition.modes.length) % definition.modes.length;
      tabButtons[target].click();
      tabButtons[target].focus();
    });
    tabs.append(tab);
    return tab;
  });
  optionSelect.addEventListener("change", () => {
    if (submitting) return;
    if (mode.choosePlace) placeKind = optionSelect.value;
    else keywordMode = optionSelect.value;
    renderControls();
    schedule();
  });
  input.addEventListener("input", schedule);
  input.addEventListener("compositionstart", () => { composing = true; reset(); });
  input.addEventListener("compositionend", () => { composing = false; schedule(); });
  input.addEventListener("keydown", (event) => {
    if (event.key === "Enter") {
      event.preventDefault();
      if (!event.isComposing && !composing) void search();
    }
  });
  next.addEventListener("click", () => { if (!loading && !submitting && hasMore) void search(page + 1); });
  retry.addEventListener("click", () => { void search(lastRequest?.pageNo ?? 1); });
  confirm.addEventListener("click", async () => {
    if (confirm.disabled || !alive() || composing) return;
    const selection = selected;
    const text = selectionText(mode, placeKind);
    submitting = true;
    input.disabled = true;
    optionSelect.disabled = true;
    tabButtons.forEach((tab) => { tab.disabled = true; });
    updateConfirm();
    try {
      await context.confirmSelection({ reference: selection.reference, text });
      if (alive()) { selected = null; status.textContent = t("已提交查询。", "Query submitted."); }
    } catch {
      if (alive()) status.textContent = t("查询未提交，请重试。", "The query was not submitted. Please retry.");
    } finally {
      if (alive()) {
        submitting = false;
        input.disabled = false;
        optionSelect.disabled = false;
        tabButtons.forEach((tab) => { tab.disabled = false; });
        updateConfirm();
      }
    }
  });
  function dispose() {
    if (disposed) return;
    disposed = true;
    generation++;
    clearTimeout(timer);
    context.signal?.removeEventListener("abort", dispose);
    sheet.remove();
    container.remove();
  }
  context.signal?.addEventListener("abort", dispose, { once: true });
  renderControls();
  reset();
  if (context.signal?.aborted) dispose();
  else input.focus({ preventScroll: true });
  return { dispose };
}

export default {
  apiVersion: 1,
  module: "cvci-entities.v1",
  surfaces: surfaceDefinitions.map((definition) => ({
    id: definition.id,
    slot: "composer-picker",
    title: definition.title,
    mount: (root, context) => mountPicker(root, context, definition),
  })),
};
