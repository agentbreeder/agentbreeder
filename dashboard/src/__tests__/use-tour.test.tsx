/**
 * Tests for the first-run tour hook (issue #465).
 *
 * Phase 4 update: the tour no longer auto-opens on first mount — the
 * GetStartedChecklist is now the first-run guide. The tour is available
 * on-demand via the shell's "Restart tour" link (open()). Storage key
 * bumped from v1 → v2 so existing completed-tour flags don't cross-pollinate.
 *
 * Covers:
 *   1. Tour does NOT auto-open on first mount (new behavior).
 *   2. hasCompleted reflects the localStorage flag.
 *   3. open() shows the tour (the "Restart tour" path).
 *   4. dismiss() hides the tour and persists the flag.
 */
import { describe, expect, it, beforeEach } from "vitest";
import { renderHook, act } from "@testing-library/react";
import type { ReactNode } from "react";
import { TourProvider, useTour } from "@/hooks/use-tour";

const STORAGE_KEY = "ag-tour-completed-v2";

const wrapper = ({ children }: { children: ReactNode }) => (
  <TourProvider>{children}</TourProvider>
);

beforeEach(() => {
  localStorage.clear();
});

describe("useTour", () => {
  it("does NOT auto-open on first mount (checklist is now the first-run guide)", () => {
    const { result } = renderHook(() => useTour(), { wrapper });
    expect(result.current.isOpen).toBe(false);
    expect(result.current.hasCompleted).toBe(false);
  });

  it("hasCompleted is true when localStorage flag is already set", () => {
    localStorage.setItem(STORAGE_KEY, "1");
    const { result } = renderHook(() => useTour(), { wrapper });
    expect(result.current.isOpen).toBe(false);
    expect(result.current.hasCompleted).toBe(true);
  });

  it("open() shows the tour (restart-tour path)", () => {
    const { result } = renderHook(() => useTour(), { wrapper });
    expect(result.current.isOpen).toBe(false);

    act(() => {
      result.current.open();
    });

    expect(result.current.isOpen).toBe(true);
    // hasCompleted unaffected by open()
    expect(result.current.hasCompleted).toBe(false);
  });

  it("dismiss() persists the flag and hides the tour", () => {
    const { result } = renderHook(() => useTour(), { wrapper });

    // Open it first (simulating "Restart tour" click), then dismiss.
    act(() => {
      result.current.open();
    });
    expect(result.current.isOpen).toBe(true);

    act(() => {
      result.current.dismiss();
    });

    expect(result.current.isOpen).toBe(false);
    expect(result.current.hasCompleted).toBe(true);
    expect(localStorage.getItem(STORAGE_KEY)).toBe("1");
  });

  it("open() reopens the tour even after a previous dismiss (restart-tour path)", () => {
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
