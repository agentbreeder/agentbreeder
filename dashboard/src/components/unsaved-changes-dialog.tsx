import { ConfirmDialog } from "@/components/ui/confirm-dialog";

interface UnsavedChangesDialogProps {
  /** Whether the dialog should be shown (from useUnsavedChanges().isBlocked). */
  isBlocked: boolean;
  /** Called when the user confirms they want to leave. */
  onConfirm: () => void;
  /** Called when the user cancels and stays on the page. */
  onCancel: () => void;
}

/**
 * A pre-configured confirmation dialog for unsaved changes warnings.
 * Designed to work with the `useUnsavedChanges` hook.
 *
 * Usage:
 * ```tsx
 * const { isBlocked, confirmNavigation, cancelNavigation } = useUnsavedChanges();
 * // ...
 * <UnsavedChangesDialog
 *   isBlocked={isBlocked}
 *   onConfirm={confirmNavigation}
 *   onCancel={cancelNavigation}
 * />
 * ```
 */
export function UnsavedChangesDialog({
  isBlocked,
  onConfirm,
  onCancel,
}: UnsavedChangesDialogProps) {
  return (
    <ConfirmDialog
      open={isBlocked}
      onOpenChange={(open) => {
        if (!open) onCancel();
      }}
      title="Unsaved changes"
      description="You have unsaved changes that will be lost if you leave this page. Are you sure you want to continue?"
      confirmLabel="Discard changes"
      cancelLabel="Stay on page"
      variant="warning"
      onConfirm={onConfirm}
      onCancel={onCancel}
    />
  );
}
