import { useState, useCallback, createContext, useContext } from "react";

export type ToastVariant = "default" | "success" | "error" | "info" | "warning";

export interface Toast {
  id: string;
  title: string;
  description?: string;
  variant: ToastVariant;
  duration: number;
}

export interface ToastInput {
  title: string;
  description?: string;
  variant?: ToastVariant;
  /** Auto-dismiss duration in milliseconds. Defaults to 5000. */
  duration?: number;
}

export interface ToastContextValue {
  toasts: Toast[];
  toast: (input: ToastInput) => void;
  dismiss: (id: string) => void;
}

const MAX_VISIBLE_TOASTS = 3;
const DEFAULT_DURATION = 5000;

let toastCounter = 0;

export const ToastContext = createContext<ToastContextValue | null>(null);

export function useToastState(): ToastContextValue {
  const [toasts, setToasts] = useState<Toast[]>([]);

  const dismiss = useCallback((id: string) => {
    setToasts((prev) => prev.filter((t) => t.id !== id));
  }, []);

  const toast = useCallback(
    (input: ToastInput) => {
      const id = `toast-${++toastCounter}`;
      const duration = input.duration ?? DEFAULT_DURATION;
      const newToast: Toast = {
        id,
        title: input.title,
        description: input.description,
        variant: input.variant ?? "default",
        duration,
      };

      setToasts((prev) => {
        const next = [...prev, newToast];
        // Keep only the most recent MAX_VISIBLE_TOASTS
        if (next.length > MAX_VISIBLE_TOASTS) {
          return next.slice(next.length - MAX_VISIBLE_TOASTS);
        }
        return next;
      });

      // Auto-dismiss after the configured duration
      setTimeout(() => {
        dismiss(id);
      }, duration);
    },
    [dismiss]
  );

  return { toasts, toast, dismiss };
}

export function useToast(): ToastContextValue {
  const ctx = useContext(ToastContext);
  if (!ctx) {
    throw new Error("useToast must be used within a ToastProvider");
  }
  return ctx;
}
