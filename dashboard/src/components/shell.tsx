import { Link, NavLink, Outlet, useLocation } from "react-router-dom";
import {
  Bot,
  Wrench,
  Cpu,
  FileText,
  Search,
  Moon,
  Sun,
  Monitor,
  Activity,
  ChevronRight,
  LogOut,
  Settings,
} from "lucide-react";
import { useState, useEffect, useRef } from "react";
import { useNavigate } from "react-router-dom";
import { useTheme } from "@/hooks/use-theme";
import { useAuth } from "@/hooks/use-auth";
import { cn } from "@/lib/utils";

const NAV = [
  { to: "/agents", icon: Bot, label: "Agents" },
  { to: "/tools", icon: Wrench, label: "Tools" },
  { to: "/models", icon: Cpu, label: "Models" },
  { to: "/prompts", icon: FileText, label: "Prompts" },
  { to: "/deploys", icon: Activity, label: "Deploys" },
] as const;

function ThemeSwitcher() {
  const { theme, setTheme } = useTheme();
  const cycle = () => {
    const next = theme === "dark" ? "light" : theme === "light" ? "system" : "dark";
    setTheme(next);
  };
  return (
    <button
      onClick={cycle}
      className="flex items-center gap-2 rounded-md px-2 py-1.5 text-xs text-muted-foreground transition-colors hover:bg-accent hover:text-accent-foreground"
      title={`Theme: ${theme}`}
    >
      {theme === "dark" ? <Moon className="size-3.5" /> : theme === "light" ? <Sun className="size-3.5" /> : <Monitor className="size-3.5" />}
      <span className="capitalize">{theme}</span>
    </button>
  );
}

function CommandSearch() {
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const inputRef = useRef<HTMLInputElement>(null);
  const navigate = useNavigate();

  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key === "k") {
        e.preventDefault();
        setOpen((v) => !v);
      }
      if (e.key === "Escape") setOpen(false);
    };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, []);

  useEffect(() => {
    if (open) {
      inputRef.current?.focus();
      setQuery("");
    }
  }, [open]);

  const submit = (e: React.FormEvent) => {
    e.preventDefault();
    if (query.trim()) {
      navigate(`/search?q=${encodeURIComponent(query.trim())}`);
      setOpen(false);
    }
  };

  if (!open) {
    return (
      <button
        onClick={() => setOpen(true)}
        className="group flex w-full items-center gap-2 rounded-md border border-border/50 bg-muted/30 px-3 py-1.5 text-xs text-muted-foreground transition-all hover:border-border hover:bg-muted/60"
      >
        <Search className="size-3.5" />
        <span className="flex-1 text-left">Search...</span>
        <kbd className="rounded border border-border bg-background px-1 font-mono text-[10px] text-muted-foreground">
          ⌘K
        </kbd>
      </button>
    );
  }

  return (
    <>
      <div className="fixed inset-0 z-50 bg-black/60 backdrop-blur-sm" onClick={() => setOpen(false)} />
      <div className="fixed inset-x-0 top-[20%] z-50 mx-auto w-full max-w-lg">
        <form
          onSubmit={submit}
          className="overflow-hidden rounded-xl border border-border bg-card shadow-2xl"
        >
          <div className="flex items-center gap-3 border-b border-border px-4 py-3">
            <Search className="size-4 text-muted-foreground" />
            <input
              ref={inputRef}
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder="Search agents, tools, prompts..."
              className="flex-1 bg-transparent text-sm outline-none placeholder:text-muted-foreground"
            />
            <kbd className="rounded border border-border px-1.5 font-mono text-[10px] text-muted-foreground">
              ESC
            </kbd>
          </div>
          <div className="px-4 py-6 text-center text-xs text-muted-foreground">
            Type to search across the registry
          </div>
        </form>
      </div>
    </>
  );
}

function Breadcrumbs() {
  const location = useLocation();
  const segments = location.pathname.split("/").filter(Boolean);
  if (segments.length === 0) return null;

  return (
    <nav className="flex items-center gap-1 text-xs text-muted-foreground">
      <Link to="/" className="transition-colors hover:text-foreground">
        Home
      </Link>
      {segments.map((seg, i) => (
        <span key={i} className="flex items-center gap-1">
          <ChevronRight className="size-3" />
          <Link
            to={`/${segments.slice(0, i + 1).join("/")}`}
            className={cn(
              "capitalize transition-colors hover:text-foreground",
              i === segments.length - 1 && "text-foreground"
            )}
          >
            {decodeURIComponent(seg)}
          </Link>
        </span>
      ))}
    </nav>
  );
}

function UserMenu() {
  const { user, logout } = useAuth();
  const navigate = useNavigate();

  if (!user) return null;

  const initials = user.name
    .split(" ")
    .map((w) => w[0])
    .join("")
    .slice(0, 2)
    .toUpperCase();

  const handleLogout = () => {
    logout();
    navigate("/login");
  };

  return (
    <div className="flex items-center gap-2 rounded-md px-2 py-1.5">
      <div className="flex size-6 items-center justify-center rounded-full bg-foreground text-[10px] font-medium text-background">
        {initials}
      </div>
      <div className="flex-1 min-w-0">
        <div className="truncate text-xs font-medium">{user.name}</div>
        <div className="truncate text-[10px] text-muted-foreground">{user.team}</div>
      </div>
      <button
        onClick={handleLogout}
        title="Sign out"
        className="rounded p-1 text-muted-foreground transition-colors hover:bg-accent hover:text-foreground"
      >
        <LogOut className="size-3" />
      </button>
    </div>
  );
}

export default function Shell() {
  return (
    <div className="flex h-screen overflow-hidden bg-background">
      {/* Sidebar */}
      <aside className="flex w-56 flex-col border-r border-border bg-sidebar">
        {/* Logo */}
        <div className="flex h-14 items-center gap-2.5 px-4">
          <div className="flex size-7 items-center justify-center rounded-lg bg-foreground">
            <Bot className="size-4 text-background" />
          </div>
          <span className="text-sm font-semibold tracking-tight">Agent Garden</span>
        </div>

        {/* Search */}
        <div className="px-3 pb-2">
          <CommandSearch />
        </div>

        {/* Nav */}
        <nav className="flex-1 space-y-0.5 px-3 pt-2">
          {NAV.map(({ to, icon: Icon, label }) => (
            <NavLink
              key={to}
              to={to}
              className={({ isActive }) =>
                cn(
                  "flex items-center gap-2.5 rounded-md px-2 py-1.5 text-sm transition-colors",
                  isActive
                    ? "bg-accent font-medium text-accent-foreground"
                    : "text-muted-foreground hover:bg-accent/50 hover:text-accent-foreground"
                )
              }
            >
              <Icon className="size-4" />
              {label}
            </NavLink>
          ))}

          {/* Separator */}
          <div className="!my-2 h-px bg-border" />

          <NavLink
            to="/settings"
            className={({ isActive }) =>
              cn(
                "flex items-center gap-2.5 rounded-md px-2 py-1.5 text-sm transition-colors",
                isActive
                  ? "bg-accent font-medium text-accent-foreground"
                  : "text-muted-foreground hover:bg-accent/50 hover:text-accent-foreground"
              )
            }
          >
            <Settings className="size-4" />
            Settings
          </NavLink>
        </nav>

        {/* Footer */}
        <div className="space-y-1 border-t border-border px-3 py-3">
          <UserMenu />
          <ThemeSwitcher />
          <div className="px-2 text-[10px] text-muted-foreground/60">
            Agent Garden v0.1
          </div>
        </div>
      </aside>

      {/* Main */}
      <main className="flex flex-1 flex-col overflow-hidden">
        <header className="flex h-14 shrink-0 items-center border-b border-border px-6">
          <Breadcrumbs />
        </header>
        <div className="flex-1 overflow-y-auto">
          <Outlet />
        </div>
      </main>
    </div>
  );
}
