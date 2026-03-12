/**
 * BaseNode — shared wrapper for all canvas node types.
 * Provides the colored header, icon, handles, and selection outline.
 */

import { type ReactNode } from "react";
import { Handle, Position } from "@xyflow/react";
import {
  Bot,
  Cpu,
  Wrench,
  Server,
  FileText,
  Brain,
  Database,
  Shield,
} from "lucide-react";
import { cn } from "@/lib/utils";
import { type CanvasNodeType, NODE_STYLES } from "../types";

const ICON_MAP: Record<string, typeof Bot> = {
  Bot,
  Cpu,
  Wrench,
  Server,
  FileText,
  Brain,
  Database,
  Shield,
};

interface BaseNodeProps {
  nodeType: CanvasNodeType;
  title: string;
  subtitle?: string;
  selected?: boolean;
  children?: ReactNode;
  /** Whether to show a target (left) handle */
  showTarget?: boolean;
  /** Whether to show a source (right) handle */
  showSource?: boolean;
}

export function BaseNode({
  nodeType,
  title,
  subtitle,
  selected,
  children,
  showTarget = true,
  showSource = true,
}: BaseNodeProps) {
  const style = NODE_STYLES[nodeType];
  const IconComp = ICON_MAP[style.icon] ?? Bot;

  return (
    <div
      className={cn(
        "min-w-[180px] max-w-[220px] rounded-lg border-2 bg-card shadow-md transition-shadow",
        style.borderColor,
        selected && "ring-2 ring-blue-500 ring-offset-1 ring-offset-background shadow-lg"
      )}
    >
      {/* Colored header */}
      <div
        className={cn(
          "flex items-center gap-2 rounded-t-md px-3 py-2",
          style.bgColor
        )}
      >
        <IconComp className={cn("size-3.5 shrink-0", style.color)} />
        <div className="min-w-0 flex-1">
          <div className={cn("truncate text-xs font-semibold", style.color)}>
            {title}
          </div>
          {subtitle && (
            <div className="truncate text-[10px] text-muted-foreground">
              {subtitle}
            </div>
          )}
        </div>
      </div>

      {/* Body */}
      {children && (
        <div className="px-3 py-2 text-[10px] text-muted-foreground">
          {children}
        </div>
      )}

      {/* Handles */}
      {showTarget && (
        <Handle
          type="target"
          position={Position.Left}
          className="!size-2.5 !rounded-full !border-2 !border-background !bg-muted-foreground/50"
        />
      )}
      {showSource && (
        <Handle
          type="source"
          position={Position.Right}
          className="!size-2.5 !rounded-full !border-2 !border-background !bg-muted-foreground/50"
        />
      )}
    </div>
  );
}
