import { useState, useCallback } from "react";
import { useNavigate } from "react-router-dom";
import { Bot, Wrench, Cpu, FileText, Plus, ArrowLeft } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogDescription,
  DialogFooter,
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

type ResourceType = (typeof RESOURCE_TYPES)[number];

/** Slug-friendly pattern: lowercase letters, digits, and hyphens only. */
const SLUG_REGEX = /^[a-z0-9]+(?:-[a-z0-9]+)*$/;

function validateName(value: string): string | null {
  if (!value.trim()) {
    return "Name is required";
  }
  if (!SLUG_REGEX.test(value)) {
    return "Name must be lowercase with hyphens only (e.g. my-resource)";
  }
  return null;
}

/**
 * "New..." button that opens a dialog with resource type selector,
 * then a name input with validation.
 */
export function NewResourceDialog({
  open: controlledOpen,
  onOpenChange: controlledOnOpenChange,
}: {
  open?: boolean;
  onOpenChange?: (open: boolean) => void;
} = {}) {
  const [internalOpen, setInternalOpen] = useState(false);
  const open = controlledOpen ?? internalOpen;
  const setOpen = controlledOnOpenChange ?? setInternalOpen;
  const navigate = useNavigate();

  const [selectedType, setSelectedType] = useState<ResourceType | null>(null);
  const [name, setName] = useState("");
  const [nameError, setNameError] = useState<string | null>(null);
  const [nameTouched, setNameTouched] = useState(false);

  const reset = useCallback(() => {
    setSelectedType(null);
    setName("");
    setNameError(null);
    setNameTouched(false);
  }, []);

  const handleOpenChange = (val: boolean) => {
    setOpen(val);
    if (!val) reset();
  };

  const handleSelectType = (rt: ResourceType) => {
    setSelectedType(rt);
    setName("");
    setNameError(null);
    setNameTouched(false);
  };

  const handleNameBlur = () => {
    setNameTouched(true);
    setNameError(validateName(name));
  };

  const handleCreate = () => {
    const error = validateName(name);
    if (error) {
      setNameError(error);
      setNameTouched(true);
      return;
    }
    if (!selectedType) return;
    handleOpenChange(false);
    navigate(`${selectedType.path}?create=true&name=${encodeURIComponent(name)}`);
  };

  return (
    <Dialog open={open} onOpenChange={handleOpenChange}>
      <DialogTrigger
        render={<Button size="sm" />}
      >
        <Plus className="size-3" data-icon="inline-start" />
        New...
      </DialogTrigger>
      <DialogContent className="sm:max-w-md">
        {selectedType ? (
          <>
            <DialogHeader>
              <DialogTitle>
                <button
                  onClick={() => setSelectedType(null)}
                  className="mr-2 inline-flex items-center gap-1 text-muted-foreground hover:text-foreground transition-colors"
                >
                  <ArrowLeft className="size-3.5" />
                </button>
                New {selectedType.label}
              </DialogTitle>
              <DialogDescription>
                Choose a name for your new {selectedType.label.toLowerCase()}.
              </DialogDescription>
            </DialogHeader>

            <div className="space-y-1.5">
              <label htmlFor="resource-name" className="text-xs font-medium">
                Name
              </label>
              <Input
                id="resource-name"
                placeholder="e.g. my-resource-name"
                value={name}
                onChange={(e) => {
                  setName(e.target.value);
                  // Clear error while typing if previously touched
                  if (nameTouched && nameError) {
                    const err = validateName(e.target.value);
                    if (!err) setNameError(null);
                  }
                }}
                onBlur={handleNameBlur}
                onKeyDown={(e) => {
                  if (e.key === "Enter") handleCreate();
                }}
                className={cn(
                  "h-8 text-xs",
                  nameTouched && nameError && "border-destructive focus-visible:ring-destructive/30"
                )}
                autoFocus
              />
              {nameTouched && nameError && (
                <p className="text-[11px] text-destructive">{nameError}</p>
              )}
              <p className="text-[10px] text-muted-foreground">
                Lowercase letters, numbers, and hyphens only.
              </p>
            </div>

            <DialogFooter>
              <Button
                variant="outline"
                size="sm"
                onClick={() => setSelectedType(null)}
              >
                Back
              </Button>
              <Button
                size="sm"
                onClick={handleCreate}
                disabled={nameTouched && !!nameError}
              >
                Continue
              </Button>
            </DialogFooter>
          </>
        ) : (
          <>
            <DialogHeader>
              <DialogTitle>Create New Resource</DialogTitle>
              <DialogDescription>
                Choose a resource type to get started.
              </DialogDescription>
            </DialogHeader>

            <div className="grid gap-2">
              {RESOURCE_TYPES.map((rt) => {
                const Icon = rt.icon;
                return (
                  <button
                    key={rt.type}
                    onClick={() => handleSelectType(rt)}
                    className={cn(
                      "flex items-center gap-3 rounded-lg border border-border p-3 text-left transition-all",
                      "hover:border-foreground/20 hover:bg-muted/30"
                    )}
                  >
                    <div
                      className={cn(
                        "flex size-9 shrink-0 items-center justify-center rounded-lg border",
                        rt.color
                      )}
                    >
                      <Icon className="size-4" />
                    </div>
                    <div className="min-w-0 flex-1">
                      <div className="text-sm font-medium">{rt.label}</div>
                      <p className="text-xs text-muted-foreground">{rt.description}</p>
                    </div>
                  </button>
                );
              })}
            </div>
          </>
        )}
      </DialogContent>
    </Dialog>
  );
}
