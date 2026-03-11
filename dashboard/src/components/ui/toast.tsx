import * as React from "react";
import { useState, useEffect } from "react";
import { XIcon, CheckCircle2, AlertCircle, Info, AlertTriangle } from "lucide-react";
import { cn } from "@/lib/utils";
import { ToastContext, useToastState } from "@/hooks/use-toast";
import type { Toast as ToastType, ToastVariant } from "@/hooks/use-toast";

const TOAST_DURATION = 5000;

const variantStyles: Record<ToastVariant, string> = {
  success:
    "border-green-500/30 bg-green-50 text-green-900 dark:border-green-500/30 dark:bg-green-950 dark:text-green-100",
  error:
    "border-red-500/30 bg-red-50 text-red-900 dark:border-red-500/30 dark:bg-red-950 dark:text-red-100",
  info:
    "border-blue-500/30 bg-blue-50 text-blue-900 dark:border-blue-500/30 dark:bg-blue-950 dark:text-blue-100",
  warning:
    "border-amber-500/30 bg-amber-50 text-amber-900 dark:border-amber-500/30 dark:bg-amber-950 dark:text-amber-100",
};

const variantProgressStyles: Record<ToastVariant, string> = {
  success: "bg-green-500",
  error: "bg-red-500",
  info: "bg-blue-500",
  warning: "bg-amber-500",
};

const variantIcons: Record<ToastVariant, React.ComponentType<{ className?: string }>> = {
  success: CheckCircle2,
  error: AlertCircle,
  info: Info,
  warning: AlertTriangle,
};

function ToastItem({
  toast,
  onDismiss,
}: {
  toast: ToastType;
  onDismiss: (id: string) => void;
}) {
  const [entering, setEntering] = useState(true);
  const [exiting, setExiting] = useState(false);
  const [progress, setProgress] = useState(100);
  const Icon = variantIcons[toast.variant];

  useEffect(() => {
    // Trigger enter animation
    const enterTimer = setTimeout(() => setEntering(false), 10);
    return () => clearTimeout(enterTimer);
  }, []);

  useEffect(() => {
    // Progress bar countdown
    const startTime = Date.now();
    const interval = setInterval(() => {
      const elapsed = Date.now() - startTime;
      const remaining = Math.max(0, 100 - (elapsed / TOAST_DURATION) * 100);
      setProgress(remaining);
      if (remaining <= 0) {
        clearInterval(interval);
      }
    }, 50);
    return () => clearInterval(interval);
  }, []);

  const handleDismiss = () => {
    setExiting(true);
    setTimeout(() => onDismiss(toast.id), 150);
  };

  return (
    <div
      data-slot="toast"
      className={cn(
        "pointer-events-auto relative w-80 overflow-hidden rounded-lg border shadow-lg transition-all duration-150",
        variantStyles[toast.variant],
        entering && "translate-x-full opacity-0",
        exiting && "opacity-0",
        !entering && !exiting && "translate-x-0 opacity-100"
      )}
    >
      <div className="flex items-start gap-3 p-3">
        <Icon className="mt-0.5 size-4 shrink-0" />
        <div className="min-w-0 flex-1">
          <p className="text-sm font-medium">{toast.title}</p>
          {toast.description && (
            <p className="mt-0.5 text-xs opacity-80">{toast.description}</p>
          )}
        </div>
        <button
          onClick={handleDismiss}
          className="shrink-0 rounded p-0.5 opacity-60 transition-opacity hover:opacity-100"
        >
          <XIcon className="size-3.5" />
          <span className="sr-only">Close</span>
        </button>
      </div>
      {/* Progress bar */}
      <div className="h-0.5 w-full bg-black/5 dark:bg-white/5">
        <div
          className={cn("h-full transition-none", variantProgressStyles[toast.variant])}
          style={{ width: `${progress}%` }}
        />
      </div>
    </div>
  );
}

function ToastContainer() {
  const ctx = React.useContext(ToastContext);
  if (!ctx) return null;
  const { toasts, dismiss } = ctx;

  if (toasts.length === 0) return null;

  return (
    <div
      data-slot="toast-container"
      className="fixed bottom-4 right-4 z-50 flex flex-col gap-2"
    >
      {toasts.map((t) => (
        <ToastItem key={t.id} toast={t} onDismiss={dismiss} />
      ))}
    </div>
  );
}

function ToastProvider({ children }: { children: React.ReactNode }) {
  const state = useToastState();

  return (
    <ToastContext.Provider value={state}>
      {children}
      <ToastContainer />
    </ToastContext.Provider>
  );
}

export { ToastProvider, ToastContainer, ToastItem };
