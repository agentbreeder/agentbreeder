import { useEffect, useRef, useCallback } from "react";
import { useNavigate } from "react-router-dom";

const COMBO_TIMEOUT = 500;

interface KeyboardShortcutsOptions {
  onShowHelp?: () => void;
}

/**
 * Global keyboard shortcut handler.
 *
 * Supports single-key and two-key combos (e.g., "g a" for navigate to /agents).
 * Ignores keypresses when the user is focused on an input, textarea, or
 * contenteditable element.
 */
export function useKeyboardShortcuts({ onShowHelp }: KeyboardShortcutsOptions = {}) {
  const navigate = useNavigate();
  const pendingKeyRef = useRef<string | null>(null);
  const timeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  const clearPending = useCallback(() => {
    pendingKeyRef.current = null;
    if (timeoutRef.current) {
      clearTimeout(timeoutRef.current);
      timeoutRef.current = null;
    }
  }, []);

  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      // Skip when typing in an input
      const target = e.target as HTMLElement;
      if (
        target.tagName === "INPUT" ||
        target.tagName === "TEXTAREA" ||
        target.tagName === "SELECT" ||
        target.isContentEditable
      ) {
        return;
      }

      // Skip if modifier keys are held (allow Cmd+K etc. to work normally)
      if (e.metaKey || e.ctrlKey || e.altKey) {
        return;
      }

      const key = e.key.toLowerCase();

      // Two-key combo: check if we have a pending first key
      if (pendingKeyRef.current === "g") {
        clearPending();
        e.preventDefault();

        switch (key) {
          case "a":
            navigate("/agents");
            return;
          case "t":
            navigate("/tools");
            return;
          case "m":
            navigate("/models");
            return;
          case "p":
            navigate("/prompts");
            return;
          case "d":
            navigate("/deploys");
            return;
          default:
            return;
        }
      }

      // Start a combo
      if (key === "g") {
        e.preventDefault();
        pendingKeyRef.current = "g";
        timeoutRef.current = setTimeout(clearPending, COMBO_TIMEOUT);
        return;
      }

      // Single-key shortcuts
      if (key === "/") {
        e.preventDefault();
        // Find and focus the search input or search trigger button
        const searchInput = document.querySelector<HTMLElement>(
          'input[placeholder*="Search"]'
        );
        const searchButton = document.querySelector<HTMLElement>(
          '[data-slot="command-search"]'
        );
        if (searchInput) {
          searchInput.focus();
        } else if (searchButton) {
          searchButton.click();
        }
        return;
      }

      if (key === "n") {
        e.preventDefault();
        // Focus the first "New" or "Create" button on the page
        const newButton = document.querySelector<HTMLElement>(
          '[data-slot="new-button"], button[data-action="new"]'
        );
        if (newButton) {
          newButton.focus();
          newButton.click();
        }
        return;
      }

      if (key === "?") {
        e.preventDefault();
        onShowHelp?.();
        return;
      }
    };

    window.addEventListener("keydown", handler);
    return () => {
      window.removeEventListener("keydown", handler);
      clearPending();
    };
  }, [navigate, onShowHelp, clearPending]);
}

/** Shortcut definitions for display in the help dialog. */
export const KEYBOARD_SHORTCUTS = [
  {
    category: "Navigation",
    shortcuts: [
      { keys: ["g", "a"], description: "Go to Agents" },
      { keys: ["g", "t"], description: "Go to Tools" },
      { keys: ["g", "m"], description: "Go to Models" },
      { keys: ["g", "p"], description: "Go to Prompts" },
      { keys: ["g", "d"], description: "Go to Deploys" },
    ],
  },
  {
    category: "Actions",
    shortcuts: [
      { keys: ["n"], description: "New / Create" },
    ],
  },
  {
    category: "Search",
    shortcuts: [
      { keys: ["/"], description: "Focus search" },
      { keys: ["?"], description: "Show keyboard shortcuts" },
    ],
  },
] as const;
