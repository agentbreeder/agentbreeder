import { describe, expect, it, beforeEach, afterEach, vi } from "vitest";
import { renderHook, act } from "@testing-library/react";
import { useDeployStream } from "@/hooks/useDeployStream";

class FakeEventSource {
  static instances: FakeEventSource[] = [];
  url: string;
  listeners: Record<string, (ev: MessageEvent) => void> = {};
  onerror: ((ev: Event) => void) | null = null;
  close = vi.fn();
  constructor(url: string) {
    this.url = url;
    FakeEventSource.instances.push(this);
  }
  addEventListener(event: string, cb: (ev: MessageEvent) => void) {
    this.listeners[event] = cb;
  }
  emit(eventName: string, data: object) {
    this.listeners[eventName]?.({ data: JSON.stringify(data) } as MessageEvent);
  }
  triggerError() {
    this.onerror?.(new Event("error"));
  }
}

beforeEach(() => {
  (globalThis as unknown as { EventSource: typeof FakeEventSource }).EventSource =
    FakeEventSource;
  FakeEventSource.instances = [];
});
afterEach(() => {
  vi.useRealTimers();
});

describe("useDeployStream", () => {
  it("opens EventSource on mount", () => {
    renderHook(() => useDeployStream("j-1"));
    expect(FakeEventSource.instances).toHaveLength(1);
    expect(FakeEventSource.instances[0].url).toContain("/deployments/j-1/stream");
  });

  it("does not open EventSource when jobId is null", () => {
    renderHook(() => useDeployStream(null));
    expect(FakeEventSource.instances).toHaveLength(0);
  });

  it("dispatches events to the consumer", () => {
    const onEvent = vi.fn();
    renderHook(() => useDeployStream("j-1", { onEvent }));
    act(() =>
      FakeEventSource.instances[0].emit("phase", {
        type: "phase",
        job_id: "j-1",
        timestamp: "",
        phase: "building",
        step: null,
        total: null,
        message: null,
        level: null,
        endpoint_url: null,
        error_code: null,
      }),
    );
    expect(onEvent).toHaveBeenCalledWith(
      expect.objectContaining({ phase: "building" }),
    );
  });

  it("closes on unmount", () => {
    const { unmount } = renderHook(() => useDeployStream("j-1"));
    unmount();
    expect(FakeEventSource.instances[0].close).toHaveBeenCalled();
  });

  it("returns disconnected status after max retries exceeded", () => {
    vi.useFakeTimers();
    const { result } = renderHook(() => useDeployStream("j-1"));

    // Trigger 4 errors total (exhausts 3 retries + initial attempt)
    for (let i = 0; i < 4; i++) {
      act(() => FakeEventSource.instances.at(-1)?.triggerError());
    }

    expect(result.current.status).toBe("disconnected");
    vi.useRealTimers();
  });
});
