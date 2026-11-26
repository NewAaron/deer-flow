import { expect, rs, test } from "@rstest/core";

import type { SurfaceContext } from "@/core/extensions/contracts";
import { mountSurface } from "@/core/extensions/surfaces";

test("surface backend failures retain HTTP status and machine code", async () => {
  const failure = Object.assign(new Error("Plugin action unavailable (403)"), {
    status: 403,
    code: "mcp_read_access_denied",
  });
  let callLater!: SurfaceContext["callBackend"];
  const cleanup = mountSurface(
    document.createElement("div"),
    {
      id: "picker",
      slot: "composer-picker",
      title: "Picker",
      mount(_root, context) {
        callLater = context.callBackend;
        return { dispose: rs.fn() };
      },
    },
    {
      namespace: "cvci-entities",
      locale: "zh",
      settings: {},
      callBackend: rs.fn(async () => {
        throw failure;
      }),
    },
    rs.fn(),
  );
  await expect(callLater("search_subjects", {})).rejects.toBe(failure);
  cleanup();
});

test("picker confirmation is revoked with the surface lifetime", async () => {
  const confirm = rs.fn(async () => undefined);
  let confirmLater!: NonNullable<SurfaceContext["confirmSelection"]>;
  const cleanup = mountSurface(
    document.createElement("div"),
    {
      id: "record",
      slot: "composer-picker",
      title: "Record",
      mount(_root, context) {
        confirmLater = context.confirmSelection!;
        return { dispose: rs.fn() };
      },
    },
    {
      namespace: "cvci-entities",
      locale: "zh",
      settings: {},
      callBackend: rs.fn(),
      confirmSelection: confirm,
    },
    rs.fn(),
  );
  const selection = {
    reference: {
      namespace: "cvci-entities",
      provider: "vehicle",
      id: "a",
      label: "车辆",
    },
    text: "查询",
  };
  await confirmLater(selection);
  cleanup();
  await expect(confirmLater(selection)).rejects.toThrow();
  expect(confirm).toHaveBeenCalledTimes(1);
});

test("surface cleanup aborts outstanding work and rejects late backend calls", async () => {
  const container = document.createElement("div");
  const dispose = rs.fn();
  const backend = rs.fn(async () => ({}));
  let callLater: (
    name: string,
    payload: Record<string, unknown>,
  ) => Promise<unknown> = rs.fn();
  let signal: AbortSignal | undefined;
  const cleanup = mountSurface(
    container,
    {
      id: "picker",
      slot: "page",
      title: "Picker",
      mount(root, context) {
        signal = context.signal;
        callLater = context.callBackend;
        root.textContent = "PLUGIN UI";
        return { dispose };
      },
    },
    {
      namespace: "community.example",
      locale: "en",
      settings: {},
      threadId: "a",
      callBackend: backend,
    },
    rs.fn(),
  );
  expect(container.shadowRoot?.textContent).toBe("PLUGIN UI");
  await callLater("search", {});
  cleanup();
  cleanup();
  await expect(callLater("search", {})).rejects.toThrow();
  expect(backend).toHaveBeenCalledTimes(1);
  expect(signal?.aborted).toBe(true);
  expect(dispose).toHaveBeenCalledTimes(1);
  expect(container.shadowRoot?.textContent).toBe("");
});

test("a failed mount is isolated and cannot leave its partial UI behind", () => {
  const error = rs.fn();
  const container = document.createElement("div");
  mountSurface(
    container,
    {
      id: "bad",
      slot: "page",
      title: "Broken",
      mount(root) {
        root.textContent = "partial";
        throw new Error("broken");
      },
    },
    {
      namespace: "community.example",
      locale: "en",
      settings: {},
      callBackend: rs.fn(),
    },
    error,
  );
  expect(error).toHaveBeenCalledTimes(1);
  expect(container.shadowRoot?.textContent).toBe("");
});

for (const failMount of [false, true]) {
  test(`surface ${failMount ? "mount failure" : "cleanup"} aborts navigation and rejects stale callbacks`, async () => {
    let openLater!: (id: string) => Promise<void>;
    let pending!: Promise<void>;
    let finish!: () => void;
    const navigate = rs.fn();
    const open = rs.fn(async (_id: string, signal: AbortSignal) => {
      await new Promise<void>((resolve) => {
        finish = resolve;
      });
      signal.throwIfAborted();
      navigate();
    });
    const cleanup = mountSurface(
      document.createElement("div"),
      {
        id: "library",
        slot: "page",
        title: "Library",
        mount(_root, context) {
          openLater = context.openConversation!;
          pending = openLater("thread");
          if (failMount) throw new Error("mount failed");
          return { dispose: rs.fn() };
        },
      },
      {
        namespace: "bookmarks",
        locale: "en",
        settings: {},
        callBackend: rs.fn(),
        openConversation: open,
      },
      rs.fn(),
    );
    if (!failMount) cleanup();
    finish();
    await expect(pending).rejects.toThrow();
    await expect(openLater("thread")).rejects.toThrow();
    expect(open).toHaveBeenCalledTimes(1);
    expect(navigate).not.toHaveBeenCalled();
  });
}
