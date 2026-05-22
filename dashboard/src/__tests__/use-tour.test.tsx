/**
 * Tests for the first-run tour hook (issue #465).
 *
 * Covers the three scenarios that matter for first-login UX:
 *   1. A user who has never seen the tour gets it auto-opened.
 *   2. A user who already dismissed it doesn't see it again on next mount.
 *   3. open() re-opens after dismiss (the "Restart tour" path).
 */
import { describe, expect, it, beforeEach } from "vitest";
import { renderHook, act } from "@testing-library/react";
import type { ReactNode } from "react";
import { TourProvider, useTour } from "@/hooks/use-tour";

const STORAGE_KEY = "ag-tour-completed-v1";

const wrapper = ({ children }: { children: ReactNode }) => (
  <TourProvider>{children}</TourProvider>
);

beforeEach(() => {
  localStorage.clear();
});

describe("useTour", () => {
  it("auto-opens on first mount when no flag is set", () => {
    const { result } = renderHook(() => useTour(), { wrapper });
    expect(result.current.isOpen).toBe(true);
    expect(result.current.hasCompleted).toBe(false);
  });

  it("does not open when localStorage flag is already set", () => {
    localStorage.setItem(STORAGE_KEY, "1");
    const { result } = renderHook(() => useTour(), { wrapper });
    expect(result.current.isOpen).toBe(false);
    expect(result.current.hasCompleted).toBe(true);
  });

  it("dismiss() persists the flag and hides the tour", () => {
    const { result } = renderHook(() => useTour(), { wrapper });
    expect(result.current.isOpen).toBe(true);

    act(() => {
      result.current.dismiss();
    });

    expect(result.current.isOpen).toBe(false);
    expect(result.current.hasCompleted).toBe(true);
    expect(localStorage.getItem(STORAGE_KEY)).toBe("1");
  });

  it("open() reopens the tour after dismiss (restart-tour path)", () => {
    localStorage.setItem(STORAGE_KEY, "1");
    const { result } = renderHook(() => useTour(), { wrapper });
    expect(result.current.isOpen).toBe(false);

    act(() => {
      result.current.open();
    });

    expect(result.current.isOpen).toBe(true);
    // hasCompleted stays true — open() only toggles visibility
    expect(result.current.hasCompleted).toBe(true);
  });
});
