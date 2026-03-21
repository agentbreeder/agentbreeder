import { useState, useEffect, type FormEvent } from "react";
import { useNavigate } from "react-router-dom";
import { Bot, ArrowRight, Loader2, AlertCircle, Eye, EyeOff } from "lucide-react";
import { useAuth } from "@/hooks/use-auth";
import { cn } from "@/lib/utils";

type Mode = "login" | "register";

/** Generates CSS for the organic cellular pattern on the branding panel. */
function breederPattern(): React.CSSProperties {
  return {
    backgroundImage: [
      // Large cellular shapes — slow drift
      `radial-gradient(ellipse 40% 50% at 20% 30%, oklch(0.35 0.02 260 / 60%) 0%, transparent 70%)`,
      `radial-gradient(ellipse 35% 45% at 75% 20%, oklch(0.30 0.03 250 / 50%) 0%, transparent 65%)`,
      `radial-gradient(ellipse 50% 35% at 55% 70%, oklch(0.28 0.02 270 / 55%) 0%, transparent 60%)`,
      // Medium cellular clusters
      `radial-gradient(circle 18% at 35% 55%, oklch(0.40 0.04 255 / 40%) 0%, transparent 70%)`,
      `radial-gradient(circle 15% at 80% 60%, oklch(0.38 0.03 265 / 35%) 0%, transparent 65%)`,
      `radial-gradient(circle 20% at 15% 75%, oklch(0.32 0.02 245 / 45%) 0%, transparent 70%)`,
      // Small organic dots — like seeds
      `radial-gradient(circle 6% at 45% 25%, oklch(0.50 0.06 258 / 50%) 0%, transparent 70%)`,
      `radial-gradient(circle 5% at 65% 45%, oklch(0.45 0.05 262 / 45%) 0%, transparent 65%)`,
      `radial-gradient(circle 4% at 25% 85%, oklch(0.48 0.04 255 / 40%) 0%, transparent 60%)`,
      `radial-gradient(circle 7% at 85% 85%, oklch(0.42 0.05 268 / 50%) 0%, transparent 70%)`,
      // Veins / connections
      `conic-gradient(from 135deg at 50% 50%, oklch(0.22 0.01 260 / 30%) 0deg, transparent 40deg, oklch(0.25 0.02 255 / 20%) 90deg, transparent 130deg, oklch(0.22 0.01 265 / 25%) 200deg, transparent 240deg, oklch(0.20 0.01 258 / 15%) 300deg, transparent 360deg)`,
      // Base
      `linear-gradient(165deg, oklch(0.14 0.005 260) 0%, oklch(0.11 0.01 255) 50%, oklch(0.13 0.005 270) 100%)`,
    ].join(", "),
  };
}

function FloatingCell({ delay, x, y, size }: { delay: number; x: number; y: number; size: number }) {
  return (
    <div
      className="absolute rounded-full"
      style={{
        left: `${x}%`,
        top: `${y}%`,
        width: `${size}px`,
        height: `${size}px`,
        background: `radial-gradient(circle, oklch(0.50 0.06 258 / 25%) 0%, transparent 70%)`,
        animation: `float ${8 + delay}s ease-in-out infinite`,
        animationDelay: `${delay}s`,
      }}
    />
  );
}

export default function LoginPage() {
  const navigate = useNavigate();
  const { login, register, user, isLoading: authLoading } = useAuth();

  const [mode, setMode] = useState<Mode>("login");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [name, setName] = useState("");
  const [team, setTeam] = useState("");
  const [error, setError] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [showPassword, setShowPassword] = useState(false);
  const [mounted, setMounted] = useState(false);

  useEffect(() => {
    setMounted(true);
  }, []);

  // Redirect if already authenticated
  useEffect(() => {
    if (user && !authLoading) navigate("/", { replace: true });
  }, [user, authLoading, navigate]);

  const handleSubmit = async (e: FormEvent) => {
    e.preventDefault();
    setError("");
    setSubmitting(true);
    try {
      if (mode === "login") {
        await login(email, password);
      } else {
        await register(email, name, password, team || "default");
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : "Something went wrong");
    } finally {
      setSubmitting(false);
    }
  };

  const switchMode = () => {
    setMode((m) => (m === "login" ? "register" : "login"));
    setError("");
  };

  if (authLoading) {
    return (
      <div className="flex h-screen items-center justify-center bg-background">
        <Loader2 className="size-5 animate-spin text-muted-foreground" />
      </div>
    );
  }

  return (
    <div className="flex h-screen overflow-hidden bg-background">
      {/* ── Left: Branding Panel ── */}
      <div
        className={cn(
          "relative hidden w-[55%] overflow-hidden lg:block",
          "transition-opacity duration-1000",
          mounted ? "opacity-100" : "opacity-0",
        )}
        style={breederPattern()}
      >
        {/* Floating cells */}
        <FloatingCell delay={0} x={20} y={25} size={120} />
        <FloatingCell delay={2} x={65} y={15} size={80} />
        <FloatingCell delay={1} x={40} y={60} size={100} />
        <FloatingCell delay={3} x={75} y={70} size={60} />
        <FloatingCell delay={1.5} x={10} y={80} size={90} />

        {/* Grid overlay — subtle structure */}
        <div
          className="absolute inset-0 opacity-[0.04]"
          style={{
            backgroundImage:
              "linear-gradient(oklch(1 0 0) 1px, transparent 1px), linear-gradient(90deg, oklch(1 0 0) 1px, transparent 1px)",
            backgroundSize: "60px 60px",
          }}
        />

        {/* Content */}
        <div className="relative z-10 flex h-full flex-col justify-between p-10">
          {/* Logo */}
          <div className="flex items-center gap-3">
            <div className="flex size-9 items-center justify-center rounded-xl bg-white/10 backdrop-blur-sm">
              <Bot className="size-5 text-white/90" />
            </div>
            <span className="text-sm font-semibold tracking-tight text-white/90">
              AgentBreeder
            </span>
          </div>

          {/* Tagline */}
          <div className="max-w-md">
            <h1 className="text-[2.5rem] font-semibold leading-[1.1] tracking-tight text-white/95">
              Define Once.
              <br />
              Deploy Anywhere.
            </h1>
            <p className="mt-4 text-sm leading-relaxed text-white/50">
              The open platform for building, deploying, and governing
              enterprise AI agents — from a single YAML file.
            </p>
          </div>

          {/* Bottom flourish */}
          <div className="flex items-center gap-3 text-[11px] text-white/30">
            <span>v0.1 Foundation</span>
            <span className="size-1 rounded-full bg-white/20" />
            <span>Open Source</span>
            <span className="size-1 rounded-full bg-white/20" />
            <span>Framework Agnostic</span>
          </div>
        </div>
      </div>

      {/* ── Right: Form Panel ── */}
      <div className="flex flex-1 flex-col">
        {/* Top-right links */}
        <div className="flex h-14 items-center justify-end px-8">
          <button
            onClick={switchMode}
            className="text-xs text-muted-foreground transition-colors hover:text-foreground"
          >
            {mode === "login" ? "Create an account" : "Already have an account?"}
          </button>
        </div>

        {/* Centered form */}
        <div
          className={cn(
            "flex flex-1 items-center justify-center px-8 transition-all duration-500",
            mounted ? "translate-y-0 opacity-100" : "translate-y-2 opacity-0",
          )}
        >
          <div className="w-full max-w-[340px]">
            {/* Mobile logo */}
            <div className="mb-8 flex items-center gap-2.5 lg:hidden">
              <div className="flex size-7 items-center justify-center rounded-lg bg-foreground">
                <Bot className="size-4 text-background" />
              </div>
              <span className="text-sm font-semibold tracking-tight">AgentBreeder</span>
            </div>

            <h2 className="text-lg font-semibold tracking-tight">
              {mode === "login" ? "Welcome back" : "Create your account"}
            </h2>
            <p className="mt-1 text-xs text-muted-foreground">
              {mode === "login"
                ? "Sign in to your AgentBreeder account"
                : "Get started with AgentBreeder"}
            </p>

            {/* Error */}
            {error && (
              <div className="mt-5 flex items-start gap-2 rounded-lg border border-destructive/30 bg-destructive/5 px-3 py-2.5">
                <AlertCircle className="mt-px size-3.5 shrink-0 text-destructive" />
                <span className="text-xs text-destructive">{error}</span>
              </div>
            )}

            <form onSubmit={handleSubmit} className="mt-6 space-y-3">
              {mode === "register" && (
                <>
                  <div>
                    <label htmlFor="name" className="mb-1.5 block text-xs font-medium">
                      Name
                    </label>
                    <input
                      id="name"
                      type="text"
                      value={name}
                      onChange={(e) => setName(e.target.value)}
                      placeholder="Ada Lovelace"
                      required
                      autoComplete="name"
                      className="h-9 w-full rounded-lg border border-border bg-background px-3 text-sm outline-none transition-colors placeholder:text-muted-foreground/50 focus:border-foreground/30 focus:ring-1 focus:ring-foreground/10"
                    />
                  </div>
                  <div>
                    <label htmlFor="team" className="mb-1.5 block text-xs font-medium">
                      Team
                    </label>
                    <input
                      id="team"
                      type="text"
                      value={team}
                      onChange={(e) => setTeam(e.target.value)}
                      placeholder="engineering"
                      autoComplete="organization"
                      className="h-9 w-full rounded-lg border border-border bg-background px-3 text-sm outline-none transition-colors placeholder:text-muted-foreground/50 focus:border-foreground/30 focus:ring-1 focus:ring-foreground/10"
                    />
                  </div>
                </>
              )}

              <div>
                <label htmlFor="email" className="mb-1.5 block text-xs font-medium">
                  Email
                </label>
                <input
                  id="email"
                  type="email"
                  value={email}
                  onChange={(e) => setEmail(e.target.value)}
                  placeholder="you@company.com"
                  required
                  autoComplete="email"
                  className="h-9 w-full rounded-lg border border-border bg-background px-3 text-sm outline-none transition-colors placeholder:text-muted-foreground/50 focus:border-foreground/30 focus:ring-1 focus:ring-foreground/10"
                />
              </div>

              <div>
                <label htmlFor="password" className="mb-1.5 block text-xs font-medium">
                  Password
                </label>
                <div className="relative">
                  <input
                    id="password"
                    type={showPassword ? "text" : "password"}
                    value={password}
                    onChange={(e) => setPassword(e.target.value)}
                    placeholder={mode === "register" ? "Min 8 characters" : ""}
                    required
                    minLength={mode === "register" ? 8 : undefined}
                    autoComplete={mode === "login" ? "current-password" : "new-password"}
                    className="h-9 w-full rounded-lg border border-border bg-background px-3 pr-9 text-sm outline-none transition-colors placeholder:text-muted-foreground/50 focus:border-foreground/30 focus:ring-1 focus:ring-foreground/10"
                  />
                  <button
                    type="button"
                    onClick={() => setShowPassword((v) => !v)}
                    tabIndex={-1}
                    className="absolute right-2.5 top-1/2 -translate-y-1/2 text-muted-foreground/60 transition-colors hover:text-foreground"
                  >
                    {showPassword ? <EyeOff className="size-3.5" /> : <Eye className="size-3.5" />}
                  </button>
                </div>
              </div>

              <button
                type="submit"
                disabled={submitting}
                className={cn(
                  "group mt-2 flex h-9 w-full items-center justify-center gap-2 rounded-lg bg-foreground text-sm font-medium text-background transition-all",
                  submitting
                    ? "cursor-not-allowed opacity-60"
                    : "hover:opacity-90 active:scale-[0.98]",
                )}
              >
                {submitting ? (
                  <Loader2 className="size-4 animate-spin" />
                ) : (
                  <>
                    {mode === "login" ? "Sign in" : "Create account"}
                    <ArrowRight className="size-3.5 transition-transform group-hover:translate-x-0.5" />
                  </>
                )}
              </button>
            </form>

            <p className="mt-6 text-center text-[11px] text-muted-foreground/50">
              {mode === "login" ? (
                <>
                  No account?{" "}
                  <button onClick={switchMode} className="text-muted-foreground transition-colors hover:text-foreground">
                    Create one
                  </button>
                </>
              ) : (
                <>
                  Already registered?{" "}
                  <button onClick={switchMode} className="text-muted-foreground transition-colors hover:text-foreground">
                    Sign in
                  </button>
                </>
              )}
            </p>
          </div>
        </div>

        {/* Footer */}
        <div className="flex h-10 items-center justify-center px-8 text-[10px] text-muted-foreground/40">
          AgentBreeder v0.1 &middot; Open Source
        </div>
      </div>

      {/* Keyframes */}
      <style>{`
        @keyframes float {
          0%, 100% { transform: translateY(0) scale(1); opacity: 0.6; }
          50% { transform: translateY(-20px) scale(1.05); opacity: 1; }
        }
      `}</style>
    </div>
  );
}
