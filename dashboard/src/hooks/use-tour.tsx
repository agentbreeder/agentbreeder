/**
 * use-tour — first-run guided tour state (issue #465).
 *
 * Per-browser via localStorage (no backend round-trip). The "Restart tour"
 * link in the shell footer (see `components/shell.tsx`) clears the flag so
 * the user can replay the tour any time.
 *
 * Bumping the version suffix on STORAGE_KEY ("v1") re-shows the tour to
 * every user after a major Studio redesign.
 */
import {
  createContext,
  useCallback,
  useContext,
  useMemo,
  useState,
  type ReactNode,
} from "react";

// Bumped from v1 → v2 when auto-open was retired (Phase 4 onboarding checklist
// replaces the tour as the first-run guide). The value-check semantics stay the
// same; bumping forces any user who had v1 cleared to get a clean slate rather
// than seeing a now-redundant auto-open on first load.
const STORAGE_KEY = "ag-tour-completed-v2";

interface TourState {
  /** True iff the tour overlay should be visible right now. */
  isOpen: boolean;
  /** Open the tour (also used by the "Restart tour" affordance). */
  open: () => void;
  /** Hide the tour and persist the completion flag. */
  dismiss: () => void;
  /** Has the user already completed/dismissed the tour at least once? */
  hasCompleted: boolean;
}

const TourContext = createContext<TourState | null>(null);

function readCompleted(): boolean {
  try {
    return localStorage.getItem(STORAGE_KEY) === "1";
  } catch {
    return false;
  }
}

export function TourProvider({ children }: { children: ReactNode }) {
  // hasCompleted still reads from localStorage so the "Restart tour" link
  // knows whether the user has ever seen the tour.
  // isOpen initializes to false: the checklist (Phase 4) is now the first-run
  // guide; the tour is available on-demand via the shell's "Restart tour" link.
  const [hasCompleted, setHasCompleted] = useState<boolean>(() => readCompleted());
  const [isOpen, setIsOpen] = useState<boolean>(false);

  const open = useCallback(() => {
    setIsOpen(true);
  }, []);

  const dismiss = useCallback(() => {
    setIsOpen(false);
    try {
      localStorage.setItem(STORAGE_KEY, "1");
    } catch {
      // localStorage unavailable (private browsing etc.) — fine, tour
      // just re-opens on next page load. Not worth surfacing an error.
    }
    setHasCompleted(true);
  }, []);

  const value = useMemo(
    () => ({ isOpen, open, dismiss, hasCompleted }),
    [isOpen, open, dismiss, hasCompleted],
  );

  return <TourContext value={value}>{children}</TourContext>;
}

export function useTour(): TourState {
  const ctx = useContext(TourContext);
  if (!ctx) throw new Error("useTour must be used within TourProvider");
  return ctx;
}
