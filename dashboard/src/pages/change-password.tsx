import { useState, type FormEvent } from "react";
import { useNavigate } from "react-router-dom";
import { Lock, ArrowRight, Loader2, AlertCircle, CheckCircle2, Eye, EyeOff } from "lucide-react";
import { useAuth } from "@/hooks/use-auth";
import { cn } from "@/lib/utils";

/**
 * Forced password change screen.
 *
 * Reached automatically when the authenticated user has
 * ``must_change_password === true`` — typically the seeded
 * admin@agentbreeder.local account on first login (issue #464).
 *
 * Cannot be dismissed without changing the password: the route guard in
 * App.tsx redirects any other route back here while the flag is set.
 * Logging out is allowed (sign-in-as-different-user escape hatch).
 */
export default function ChangePasswordPage() {
  const navigate = useNavigate();
  const { user, changePassword, logout, isLoading: authLoading } = useAuth();

  const [oldPassword, setOldPassword] = useState("");
  const [newPassword, setNewPassword] = useState("");
  const [confirmPassword, setConfirmPassword] = useState("");
  const [showOld, setShowOld] = useState(false);
  const [showNew, setShowNew] = useState(false);
  const [error, setError] = useState("");
  const [submitting, setSubmitting] = useState(false);

  const minLength = newPassword.length >= 8;
  const differsFromOld = newPassword.length > 0 && newPassword !== oldPassword;
  const matchesConfirm = newPassword.length > 0 && newPassword === confirmPassword;
  const canSubmit = minLength && differsFromOld && matchesConfirm && !submitting;

  const handleSubmit = async (e: FormEvent) => {
    e.preventDefault();
    if (!canSubmit) return;
    setError("");
    setSubmitting(true);
    try {
      await changePassword(oldPassword, newPassword);
      navigate("/", { replace: true });
    } catch (err) {
      setError(err instanceof Error ? err.message : "Password change failed");
    } finally {
      setSubmitting(false);
    }
  };

  if (authLoading) {
    return (
      <div className="grid min-h-screen place-items-center bg-background">
        <Loader2 className="size-6 animate-spin text-muted-foreground" />
      </div>
    );
  }

  return (
    <div className="grid min-h-screen place-items-center bg-background px-4 py-12">
      <div className="w-full max-w-md space-y-6">
        <div className="space-y-3 text-center">
          <div className="mx-auto grid size-12 place-items-center rounded-2xl bg-amber-500/10 ring-1 ring-amber-500/30">
            <Lock className="size-5 text-amber-500" />
          </div>
          <h1 className="text-2xl font-semibold tracking-tight">Set a new password</h1>
          <p className="text-sm text-muted-foreground">
            {user?.email === "admin@agentbreeder.local" ? (
              <>
                You&apos;re signed in with the default seeded admin credential.
                Choose a new password before continuing — the default is publicly
                documented and unsafe to leave in place.
              </>
            ) : (
              <>An administrator requires you to rotate your password before continuing.</>
            )}
          </p>
        </div>

        <form onSubmit={handleSubmit} className="space-y-4 rounded-2xl border border-border bg-card p-6 shadow-sm">
          <Field
            label="Current password"
            value={oldPassword}
            onChange={setOldPassword}
            show={showOld}
            onToggleShow={() => setShowOld((s) => !s)}
            autoComplete="current-password"
            autoFocus
          />
          <Field
            label="New password"
            value={newPassword}
            onChange={setNewPassword}
            show={showNew}
            onToggleShow={() => setShowNew((s) => !s)}
            autoComplete="new-password"
          />
          <Field
            label="Confirm new password"
            value={confirmPassword}
            onChange={setConfirmPassword}
            show={showNew}
            onToggleShow={() => setShowNew((s) => !s)}
            autoComplete="new-password"
          />

          <ul className="space-y-1.5 text-xs text-muted-foreground">
            <Check ok={minLength}>At least 8 characters</Check>
            <Check ok={differsFromOld}>Different from your current password</Check>
            <Check ok={matchesConfirm}>Matches the confirmation</Check>
          </ul>

          {error && (
            <div className="flex items-start gap-2 rounded-lg border border-red-500/30 bg-red-500/10 p-3 text-sm text-red-600 dark:text-red-400">
              <AlertCircle className="mt-0.5 size-4 shrink-0" />
              <span>{error}</span>
            </div>
          )}

          <button
            type="submit"
            disabled={!canSubmit}
            className={cn(
              "flex w-full items-center justify-center gap-2 rounded-lg bg-primary px-4 py-2.5 text-sm font-medium text-primary-foreground transition",
              canSubmit ? "hover:bg-primary/90" : "cursor-not-allowed opacity-60",
            )}
          >
            {submitting ? (
              <Loader2 className="size-4 animate-spin" />
            ) : (
              <>
                Set new password
                <ArrowRight className="size-4" />
              </>
            )}
          </button>

          <button
            type="button"
            onClick={() => {
              logout();
              navigate("/login", { replace: true });
            }}
            className="w-full text-xs text-muted-foreground hover:text-foreground"
          >
            Sign out instead
          </button>
        </form>
      </div>
    </div>
  );
}

interface FieldProps {
  label: string;
  value: string;
  onChange: (v: string) => void;
  show: boolean;
  onToggleShow: () => void;
  autoComplete: string;
  autoFocus?: boolean;
}

function Field({ label, value, onChange, show, onToggleShow, autoComplete, autoFocus }: FieldProps) {
  return (
    <label className="block space-y-1.5">
      <span className="text-xs font-medium text-foreground">{label}</span>
      <div className="relative">
        <input
          type={show ? "text" : "password"}
          value={value}
          onChange={(e) => onChange(e.target.value)}
          autoComplete={autoComplete}
          autoFocus={autoFocus}
          required
          className="w-full rounded-lg border border-input bg-background px-3 py-2 pr-9 text-sm shadow-sm focus:border-ring focus:outline-none focus:ring-2 focus:ring-ring/30"
        />
        <button
          type="button"
          onClick={onToggleShow}
          aria-label={show ? "Hide password" : "Show password"}
          className="absolute inset-y-0 right-2 grid place-items-center text-muted-foreground hover:text-foreground"
        >
          {show ? <EyeOff className="size-4" /> : <Eye className="size-4" />}
        </button>
      </div>
    </label>
  );
}

function Check({ ok, children }: { ok: boolean; children: React.ReactNode }) {
  return (
    <li className={cn("flex items-center gap-1.5", ok ? "text-emerald-600 dark:text-emerald-400" : "")}>
      <CheckCircle2 className={cn("size-3.5", ok ? "" : "opacity-30")} />
      <span>{children}</span>
    </li>
  );
}
