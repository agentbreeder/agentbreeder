import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { Bot, Wrench, Cpu, FileText, Plus } from "lucide-react";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogDescription,
  DialogTrigger,
} from "@/components/ui/dialog";
import { cn } from "@/lib/utils";

const RESOURCE_TYPES = [
  {
    type: "agent",
    label: "Agent",
    description: "Deploy an AI agent with tools, prompts, and models",
    icon: Bot,
    path: "/agents",
    color: "bg-violet-500/10 text-violet-600 dark:text-violet-400 border-violet-500/20",
  },
  {
    type: "tool",
    label: "Tool",
    description: "Register a tool or MCP server for agents to use",
    icon: Wrench,
    path: "/tools",
    color: "bg-sky-500/10 text-sky-600 dark:text-sky-400 border-sky-500/20",
  },
  {
    type: "prompt",
    label: "Prompt",
    description: "Create a reusable prompt template with versioning",
    icon: FileText,
    path: "/prompts",
    color: "bg-emerald-500/10 text-emerald-600 dark:text-emerald-400 border-emerald-500/20",
  },
  {
    type: "model",
    label: "Model",
    description: "Register an LLM model in the organization registry",
    icon: Cpu,
    path: "/models",
    color: "bg-orange-500/10 text-orange-600 dark:text-orange-400 border-orange-500/20",
  },
] as const;

/**
 * "New..." button that opens a dialog with resource type selector.
 * Selecting a type navigates to the appropriate creation flow.
 */
export function NewResourceDialog() {
  const [open, setOpen] = useState(false);
  const navigate = useNavigate();

  const handleSelect = (path: string) => {
    setOpen(false);
    navigate(`${path}?create=true`);
  };

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger
        render={<Button size="sm" />}
      >
        <Plus className="size-3" data-icon="inline-start" />
        New...
      </DialogTrigger>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>Create New Resource</DialogTitle>
          <DialogDescription>
            Choose a resource type to get started.
          </DialogDescription>
        </DialogHeader>

        <div className="grid gap-2">
          {RESOURCE_TYPES.map(({ type, label, description, icon: Icon, path, color }) => (
            <button
              key={type}
              onClick={() => handleSelect(path)}
              className={cn(
                "flex items-center gap-3 rounded-lg border border-border p-3 text-left transition-all",
                "hover:border-foreground/20 hover:bg-muted/30"
              )}
            >
              <div
                className={cn(
                  "flex size-9 shrink-0 items-center justify-center rounded-lg border",
                  color
                )}
              >
                <Icon className="size-4" />
              </div>
              <div className="min-w-0 flex-1">
                <div className="text-sm font-medium">{label}</div>
                <p className="text-xs text-muted-foreground">{description}</p>
              </div>
            </button>
          ))}
        </div>
      </DialogContent>
    </Dialog>
  );
}
