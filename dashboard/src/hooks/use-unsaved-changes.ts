import { useState, useCallback, useEffect } from "react";
import { useBlocker } from "react-router-dom";

interface UseUnsavedChangesReturn {
  /** Whether the form has unsaved changes. */
  isDirty: boolean;
  /** Mark the form as having unsaved changes. */
  markDirty: () => void;
  /** Mark the form as clean (e.g. after a successful save). */
  markClean: () => void;
  /** Whether the confirmation dialog is currently showing. */
  isBlocked: boolean;
  /** Confirm navigation (proceed and discard changes). */
  confirmNavigation: () => void;
  /** Cancel navigation (stay on the page). */
  cancelNavigation: () => void;
}

/**
 * Hook that tracks unsaved changes and warns before navigating away.
 *
 * Handles both:
 * - Browser navigation (tab close, URL bar) via `beforeunload`
 * - React Router navigation via `useBlocker`
 *
 * Usage:
 * ```tsx
 * const { isDirty, markDirty, markClean, isBlocked, confirmNavigation, cancelNavigation } =
 *   useUnsavedChanges();
 * ```
 */
export function useUnsavedChanges(): UseUnsavedChangesReturn {
  const [isDirty, setIsDirty] = useState(false);

  const markDirty = useCallback(() => setIsDirty(true), []);
  const markClean = useCallback(() => setIsDirty(false), []);

  // Block React Router navigation when dirty
  const blocker = useBlocker(isDirty);

  const isBlocked = blocker.state === "blocked";

  const confirmNavigation = useCallback(() => {
    if (blocker.state === "blocked") {
      blocker.proceed();
    }
  }, [blocker]);

  const cancelNavigation = useCallback(() => {
    if (blocker.state === "blocked") {
      blocker.reset();
    }
  }, [blocker]);

  // Browser beforeunload warning
  useEffect(() => {
    if (!isDirty) return;

    const handler = (e: BeforeUnloadEvent) => {
      e.preventDefault();
    };

    window.addEventListener("beforeunload", handler);
    return () => window.removeEventListener("beforeunload", handler);
  }, [isDirty]);

  return {
    isDirty,
    markDirty,
    markClean,
    isBlocked,
    confirmNavigation,
    cancelNavigation,
  };
}
