import { afterEach, expect, test, rs } from "@rstest/core";
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";

import { CvciLogoutComplete } from "@/components/auth/cvci-logout-complete";

afterEach(() => {
  cleanup();
  rs.restoreAllMocks();
});
test.each(["cleared", "unchanged"])(
  "cleanup %s reaches only the fixed trusted hop",
  async (status) => {
    const navigate = rs
      .spyOn(window.location, "replace")
      .mockImplementation(() => undefined);
    globalThis.fetch = rs.fn(async () =>
      Response.json({ status }),
    ) as typeof fetch;
    render(<CvciLogoutComplete />);
    await waitFor(() =>
      expect(navigate).toHaveBeenCalledExactlyOnceWith("/auth/cvci-logout"),
    );
    expect(fetch).toHaveBeenCalledWith(
      "/api/v1/auth/logout/cleanup",
      expect.objectContaining({ method: "POST", credentials: "include" }),
    );
    expect(
      rs
        .mocked(fetch)
        .mock.calls.some(([url]) => url === "/api/v1/auth/logout"),
    ).toBe(false);
  },
);
test("unknown or offline cleanup cannot pretend success and retries only cleanup", async () => {
  const navigate = rs
    .spyOn(window.location, "replace")
    .mockImplementation(() => undefined);
  let available = false;
  globalThis.fetch = rs.fn(async () =>
    available
      ? Response.json({ status: "cleared" })
      : new Response(null, { status: 503 }),
  ) as typeof fetch;
  render(<CvciLogoutComplete />);
  await screen.findByRole("alert");
  expect(navigate).not.toHaveBeenCalled();
  available = true;
  fireEvent.click(screen.getByRole("button"));
  await waitFor(() => expect(navigate).toHaveBeenCalledOnce());
});
