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
  Clock,
  ChevronRight,
  LogOut,
  Settings,
  GripVertical,
  Server,
} from "lucide-react";
import { useState, useEffect, useRef, useCallback } from "react";
import { useNavigate } from "react-router-dom";
import { useTheme } from "@/hooks/use-theme";
import { useAuth } from "@/hooks/use-auth";
import { useKeyboardShortcuts } from "@/hooks/use-keyboard-shortcuts";
import { cn } from "@/lib/utils";
import { ToastProvider } from "@/components/ui/toast";
import { KeyboardShortcutsDialog } from "@/components/keyboard-shortcuts-dialog";
import { NewResourceDialog } from "@/components/new-resource-dialog";
import {
  Tooltip,
  TooltipTrigger,
  TooltipContent,
  TooltipProvider,
} from "@/components/ui/tooltip";

const NAV = [
  { to: "/agents", icon: Bot, label: "Agents" },
  { to: "/tools", icon: Wrench, label: "Tools" },
  { to: "/mcp-servers", icon: Server, label: "MCP Servers" },
  { to: "/models", icon: Cpu, label: "Models" },
  { to: "/prompts", icon: FileText, label: "Prompts" },
  { to: "/deploys", icon: Activity, label: "Deploys" },
  { to: "/activity", icon: Clock, label: "Activity" },
] as const;

const SIDEBAR_MIN = 48;
const SIDEBAR_DEFAULT = 256;
const SIDEBAR_MAX = 320;
const SIDEBAR_COLLAPSED_THRESHOLD = 100;
const SIDEBAR_STORAGE_KEY = "agenthub-sidebar-width";

function getSavedSidebarWidth(): number {
  try {
    const saved = localStorage.getItem(SIDEBAR_STORAGE_KEY);
    if (saved) {
      const width = parseInt(saved, 10);
      if (!isNaN(width) && width >= SIDEBAR_MIN && width <= SIDEBAR_MAX) {
        return width;
      }
    }
  } catch {
    // localStorage may be unavailable
  }
  return SIDEBAR_DEFAULT;
}

function saveSidebarWidth(width: number) {
  try {
    localStorage.setItem(SIDEBAR_STORAGE_KEY, String(width));
  } catch {
    // ignore
  }
}

function ThemeSwitcher({ collapsed }: { collapsed: boolean }) {
  const { theme, setTheme } = useTheme();
  const cycle = () => {
    const next = theme === "dark" ? "light" : theme === "light" ? "system" : "dark";
    setTheme(next);
  };
  const Icon = theme === "dark" ? Moon : theme === "light" ? Sun : Monitor;

  if (collapsed) {
    return (
      <Tooltip>
        <TooltipTrigger
          render={
            <button
              onClick={cycle}
              className="flex size-8 items-center justify-center rounded-md text-muted-foreground transition-colors hover:bg-accent hover:text-accent-foreground"
            />
          }
        >
          <Icon className="size-3.5" />
        </TooltipTrigger>
        <TooltipContent side="right">
          Theme: {theme}
        </TooltipContent>
      </Tooltip>
    );
  }

  return (
    <button
      onClick={cycle}
      className="flex items-center gap-2 rounded-md px-2 py-1.5 text-xs text-muted-foreground transition-colors hover:bg-accent hover:text-accent-foreground"
      title={`Theme: ${theme}`}
    >
      <Icon className="size-3.5" />
      <span className="capitalize">{theme}</span>
    </button>
  );
}

function CommandSearch({ collapsed }: { collapsed: boolean }) {
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
    if (collapsed) {
      return (
        <Tooltip>
          <TooltipTrigger
            render={
              <button
                onClick={() => setOpen(true)}
                className="flex size-8 items-center justify-center rounded-md text-muted-foreground transition-colors hover:bg-accent hover:text-accent-foreground"
              />
            }
          >
            <Search className="size-3.5" />
          </TooltipTrigger>
          <TooltipContent side="right">Search</TooltipContent>
        </Tooltip>
      );
    }

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

const BREADCRUMB_ICONS: Record<string, React.ComponentType<{ className?: string }>> = {
  agents: Bot,
  tools: Wrench,
  models: Cpu,
  prompts: FileText,
  deploys: Activity,
  activity: Clock,
};

function Breadcrumbs() {
  const location = useLocation();
  const segments = location.pathname.split("/").filter(Boolean);
  if (segments.length === 0) return null;

  return (
    <nav className="flex items-center gap-1 text-xs text-muted-foreground">
      <Link to="/" className="transition-colors hover:text-foreground">
        Home
      </Link>
      {segments.map((seg, i) => {
        const Icon = i === 0 ? BREADCRUMB_ICONS[seg] : undefined;
        return (
          <span key={i} className="flex items-center gap-1">
            <ChevronRight className="size-3" />
            {Icon && <Icon className="size-3" />}
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
        );
      })}
    </nav>
  );
}

function UserMenu({ collapsed }: { collapsed: boolean }) {
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

  if (collapsed) {
    return (
      <Tooltip>
        <TooltipTrigger
          render={
            <div className="flex size-8 items-center justify-center">
              <div className="flex size-6 items-center justify-center rounded-full bg-foreground text-[10px] font-medium text-background">
                {initials}
              </div>
            </div>
          }
        />
        <TooltipContent side="right">
          {user.name} ({user.team})
        </TooltipContent>
      </Tooltip>
    );
  }

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

/** Chord mode indicator shown when "g" is pressed */
function ChordIndicator({ active }: { active: boolean }) {
  if (!active) return null;

  return (
    <div className="fixed bottom-4 left-1/2 z-50 -translate-x-1/2 animate-in fade-in zoom-in-95 duration-150">
      <div className="flex items-center gap-2 rounded-lg border border-border bg-card px-3 py-2 shadow-lg">
        <kbd className="inline-flex h-5 min-w-5 items-center justify-center rounded border border-border bg-muted px-1.5 font-mono text-[11px] font-medium text-muted-foreground">
          g
        </kbd>
        <span className="text-xs text-muted-foreground">...</span>
      </div>
    </div>
  );
}

/** Nav item with tooltip support for collapsed sidebar */
function SidebarNavItem({
  to,
  icon: Icon,
  label,
  collapsed,
}: {
  to: string;
  icon: React.ComponentType<{ className?: string }>;
  label: string;
  collapsed: boolean;
}) {
  const link = (
    <NavLink
      to={to}
      className={({ isActive }) =>
        cn(
          collapsed
            ? "flex size-8 items-center justify-center rounded-md transition-colors mx-auto"
            : "flex items-center gap-2.5 rounded-md px-2 py-1.5 text-sm transition-colors",
          isActive
            ? "bg-accent font-medium text-accent-foreground"
            : "text-muted-foreground hover:bg-accent/50 hover:text-accent-foreground"
        )
      }
    >
      <Icon className="size-4 shrink-0" />
      {!collapsed && <span className="truncate">{label}</span>}
    </NavLink>
  );

  if (collapsed) {
    return (
      <Tooltip>
        <TooltipTrigger render={link} />
        <TooltipContent side="right">{label}</TooltipContent>
      </Tooltip>
    );
  }

  return link;
}

function ShellInner() {
  const [shortcutsOpen, setShortcutsOpen] = useState(false);
  const [newResourceOpen, setNewResourceOpen] = useState(false);
  const [chordActive, setChordActive] = useState(false);
  const [sidebarWidth, setSidebarWidth] = useState(getSavedSidebarWidth);
  const [isDragging, setIsDragging] = useState(false);
  const [isTransitioning, setIsTransitioning] = useState(false);
  const dragStartXRef = useRef(0);
  const dragStartWidthRef = useRef(0);

  const collapsed = sidebarWidth <= SIDEBAR_COLLAPSED_THRESHOLD;

  // Persist sidebar width
  useEffect(() => {
    saveSidebarWidth(sidebarWidth);
  }, [sidebarWidth]);

  const showHelp = useCallback(() => setShortcutsOpen(true), []);
  const openNewResource = useCallback(() => setNewResourceOpen(true), []);
  const onChordStart = useCallback(() => setChordActive(true), []);
  const onChordEnd = useCallback(() => setChordActive(false), []);

  const toggleSidebar = useCallback(() => {
    setIsTransitioning(true);
    setSidebarWidth((w) =>
      w <= SIDEBAR_COLLAPSED_THRESHOLD ? SIDEBAR_DEFAULT : SIDEBAR_MIN
    );
    setTimeout(() => setIsTransitioning(false), 200);
  }, []);

  useKeyboardShortcuts({
    onShowHelp: showHelp,
    onNewResource: openNewResource,
    onChordStart,
    onChordEnd,
    onToggleSidebar: toggleSidebar,
  });

  // Drag resize handlers
  const handleDragStart = useCallback(
    (e: React.MouseEvent) => {
      e.preventDefault();
      setIsDragging(true);
      dragStartXRef.current = e.clientX;
      dragStartWidthRef.current = sidebarWidth;
    },
    [sidebarWidth]
  );

  useEffect(() => {
    if (!isDragging) return;

    const handleDragMove = (e: MouseEvent) => {
      const delta = e.clientX - dragStartXRef.current;
      const newWidth = Math.min(
        SIDEBAR_MAX,
        Math.max(SIDEBAR_MIN, dragStartWidthRef.current + delta)
      );
      setSidebarWidth(newWidth);
    };

    const handleDragEnd = () => {
      setIsDragging(false);
      // Snap: if between min and threshold, snap to collapsed or default
      setSidebarWidth((w) => {
        if (w < SIDEBAR_COLLAPSED_THRESHOLD) return SIDEBAR_MIN;
        if (w < 160) return SIDEBAR_DEFAULT;
        return w;
      });
    };

    document.addEventListener("mousemove", handleDragMove);
    document.addEventListener("mouseup", handleDragEnd);
    document.body.style.userSelect = "none";
    document.body.style.cursor = "col-resize";

    return () => {
      document.removeEventListener("mousemove", handleDragMove);
      document.removeEventListener("mouseup", handleDragEnd);
      document.body.style.userSelect = "";
      document.body.style.cursor = "";
    };
  }, [isDragging]);

  const handleDoubleClick = useCallback(() => {
    setIsTransitioning(true);
    setSidebarWidth((w) =>
      w <= SIDEBAR_COLLAPSED_THRESHOLD ? SIDEBAR_DEFAULT : SIDEBAR_MIN
    );
    setTimeout(() => setIsTransitioning(false), 200);
  }, []);

  return (
    <TooltipProvider>
      <div className="flex h-screen overflow-hidden bg-background">
        <KeyboardShortcutsDialog
          open={shortcutsOpen}
          onOpenChange={setShortcutsOpen}
        />
        <ChordIndicator active={chordActive} />

        {/* Sidebar */}
        <aside
          style={{ width: sidebarWidth }}
          className={cn(
            "relative flex shrink-0 flex-col border-r border-border bg-sidebar overflow-hidden",
            isTransitioning && "transition-[width] duration-200 ease-in-out"
          )}
        >
          {/* Drag handle */}
          <div
            onMouseDown={handleDragStart}
            onDoubleClick={handleDoubleClick}
            className={cn(
              "absolute inset-y-0 right-0 z-10 w-1 cursor-col-resize transition-colors hover:bg-foreground/10",
              isDragging && "bg-foreground/20"
            )}
          >
            <div
              className={cn(
                "absolute right-0 top-1/2 -translate-y-1/2 translate-x-1/2 rounded-sm bg-border p-0.5 opacity-0 transition-opacity",
                "group-hover:opacity-100",
                isDragging ? "opacity-100" : "hover:opacity-100"
              )}
            >
              <GripVertical className="size-3 text-muted-foreground" />
            </div>
          </div>

          {/* Logo */}
          <div className="flex h-14 items-center gap-2.5 overflow-hidden px-3">
            <div className="flex size-7 shrink-0 items-center justify-center rounded-lg bg-foreground">
              <Bot className="size-4 text-background" />
            </div>
            {!collapsed && (
              <span className="truncate text-sm font-semibold tracking-tight">
                Agent Garden
              </span>
            )}
          </div>

          {/* Search + New */}
          {collapsed ? (
            <div className="flex flex-col items-center gap-1.5 px-1.5 pb-2">
              <CommandSearch collapsed={collapsed} />
              <NewResourceDialog
                open={newResourceOpen}
                onOpenChange={setNewResourceOpen}
              />
            </div>
          ) : (
            <div className="flex items-center gap-1.5 px-3 pb-2">
              <div className="flex-1">
                <CommandSearch collapsed={collapsed} />
              </div>
              <NewResourceDialog
                open={newResourceOpen}
                onOpenChange={setNewResourceOpen}
              />
            </div>
          )}

          {/* Nav */}
          <nav className="flex-1 space-y-0.5 overflow-hidden px-1.5 pt-2">
            {NAV.map(({ to, icon, label }) => (
              <SidebarNavItem
                key={to}
                to={to}
                icon={icon}
                label={label}
                collapsed={collapsed}
              />
            ))}

            {/* Separator */}
            <div className="!my-2 h-px bg-border" />

            <SidebarNavItem
              to="/settings"
              icon={Settings}
              label="Settings"
              collapsed={collapsed}
            />
          </nav>

          {/* Footer */}
          <div
            className={cn(
              "space-y-1 border-t border-border py-3",
              collapsed ? "flex flex-col items-center px-1.5" : "px-3"
            )}
          >
            <UserMenu collapsed={collapsed} />
            <ThemeSwitcher collapsed={collapsed} />
            {!collapsed && (
              <div className="px-2 text-[10px] text-muted-foreground/60">
                Agent Garden v0.1 &middot; Press{" "}
                <kbd className="rounded border border-border/50 px-1 font-mono">
                  ?
                </kbd>{" "}
                for shortcuts
              </div>
            )}
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
    </TooltipProvider>
  );
}

export default function Shell() {
  return (
    <ToastProvider>
      <ShellInner />
    </ToastProvider>
  );
}
