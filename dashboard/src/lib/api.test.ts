import { describe, it, expect, vi, beforeEach } from "vitest";
import { streamSSE } from "@/lib/api";

function sseResponse(body: string): Response {
  const stream = new ReadableStream<Uint8Array>({
    start(controller) {
      controller.enqueue(new TextEncoder().encode(body));
      controller.close();
    },
  });
  return new Response(stream, {
    status: 200,
    headers: { "content-type": "text/event-stream" },
  });
}

describe("streamSSE", () => {
  beforeEach(() => {
    localStorage.setItem("ag-token", "tok");
  });

  it("parses event/data frames and invokes the handler per event", async () => {
    const raw =
      "event: token\ndata: {\"text\":\"Hi\"}\n\n" +
      "event: done\ndata: {\"agent_yaml\":null}\n\n";
    vi.spyOn(globalThis, "fetch").mockResolvedValue(sseResponse(raw));

    const seen: Array<{ event: string; data: unknown }> = [];
    await streamSSE("/builders/chat/stream", { method: "POST", body: "{}" }, (e, d) =>
      seen.push({ event: e, data: d }),
    );

    expect(seen).toEqual([
      { event: "token", data: { text: "Hi" } },
      { event: "done", data: { agent_yaml: null } },
    ]);
  });
});
