"use client";

import { useState } from "react";
import { Loader2 } from "lucide-react";
import { cn } from "@/lib/utils";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogDescription,
  DialogFooter,
  DialogTrigger,
} from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";

type ConfirmDialogVariant = "danger" | "warning" | "destructive" | "default";

interface ConfirmDialogProps {
  title: string;
  description: string;
  confirmLabel?: string;
  cancelLabel?: string;
  variant?: ConfirmDialogVariant;
  onConfirm: () => void;
  onCancel?: () => void;
  trigger?: React.ReactNode;
  open?: boolean;
  onOpenChange?: (open: boolean) => void;
  /** Show a loading spinner on the confirm button and disable both buttons. */
  loading?: boolean;
}

const confirmButtonStyles: Record<string, string> = {
  danger:
    "bg-red-600 text-white hover:bg-red-700 dark:bg-red-600 dark:hover:bg-red-700",
  destructive:
    "bg-red-600 text-white hover:bg-red-700 dark:bg-red-600 dark:hover:bg-red-700",
  warning:
    "bg-amber-500 text-white hover:bg-amber-600 dark:bg-amber-500 dark:hover:bg-amber-600",
  default: "",
};

function ConfirmDialog({
  title,
  description,
  confirmLabel = "Confirm",
  cancelLabel = "Cancel",
  variant = "danger",
  onConfirm,
  onCancel,
  trigger,
  open: controlledOpen,
  onOpenChange: controlledOnOpenChange,
  loading = false,
}: ConfirmDialogProps) {
  const [uncontrolledOpen, setUncontrolledOpen] = useState(false);

  const isControlled = controlledOpen !== undefined;
  const open = isControlled ? controlledOpen : uncontrolledOpen;
  const setOpen = isControlled
    ? (v: boolean) => controlledOnOpenChange?.(v)
    : setUncontrolledOpen;

  const handleConfirm = () => {
    onConfirm();
    if (!loading) {
      setOpen(false);
    }
  };

  const handleCancel = () => {
    onCancel?.();
    setOpen(false);
  };

  return (
    <Dialog open={open} onOpenChange={loading ? undefined : setOpen}>
      {trigger && <DialogTrigger render={<span />}>{trigger}</DialogTrigger>}
      <DialogContent showCloseButton={false}>
        <DialogHeader>
          <DialogTitle>{title}</DialogTitle>
          <DialogDescription>{description}</DialogDescription>
        </DialogHeader>
        <DialogFooter>
          <Button variant="outline" onClick={handleCancel} disabled={loading}>
            {cancelLabel}
          </Button>
          <Button
            className={cn(confirmButtonStyles[variant])}
            onClick={handleConfirm}
            disabled={loading}
          >
            {loading && <Loader2 className="size-3.5 animate-spin" />}
            {confirmLabel}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

export { ConfirmDialog };
export type { ConfirmDialogProps, ConfirmDialogVariant };
