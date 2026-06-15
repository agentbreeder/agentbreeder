import { describe, it, expect, vi, beforeEach } from "vitest";
import { track, ANALYTICS_EVENTS } from "./analytics";

describe("analytics seam", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({ ok: true, json: async () => ({}) }));
  });
  it("includes the full funnel taxonomy", () => {
    expect(ANALYTICS_EVENTS).toContain("builder_session_started");
    expect(ANALYTICS_EVENTS).toContain("first_invoke");
  });
  it("POSTs the event to the ingest endpoint", () => {
    track("eject_to_code_started", { engine: "codex" });
    expect(fetch).toHaveBeenCalledWith(
      expect.stringContaining("/analytics/events"),
      expect.objectContaining({ method: "POST" }),
    );
  });
});
