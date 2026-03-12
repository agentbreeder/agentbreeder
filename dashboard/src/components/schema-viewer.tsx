import { useState } from "react";
import { ChevronRight, ChevronDown } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { cn } from "@/lib/utils";

interface JsonSchemaProperty {
  type?: string;
  description?: string;
  items?: JsonSchemaProperty;
  properties?: Record<string, JsonSchemaProperty>;
  required?: string[];
  enum?: string[];
  default?: unknown;
}

interface JsonSchema {
  type?: string;
  properties?: Record<string, JsonSchemaProperty>;
  required?: string[];
  items?: JsonSchemaProperty;
  description?: string;
}

const TYPE_COLORS: Record<string, string> = {
  string: "bg-emerald-500/10 text-emerald-600 dark:text-emerald-400 border-emerald-500/20",
  number: "bg-blue-500/10 text-blue-600 dark:text-blue-400 border-blue-500/20",
  integer: "bg-blue-500/10 text-blue-600 dark:text-blue-400 border-blue-500/20",
  boolean: "bg-orange-500/10 text-orange-600 dark:text-orange-400 border-orange-500/20",
  object: "bg-violet-500/10 text-violet-600 dark:text-violet-400 border-violet-500/20",
  array: "bg-cyan-500/10 text-cyan-600 dark:text-cyan-400 border-cyan-500/20",
};

function getTypeLabel(prop: JsonSchemaProperty): string {
  if (prop.type === "array" && prop.items?.type) {
    return `Array of ${prop.items.type}`;
  }
  return prop.type ?? "unknown";
}

function PropertyRow({
  name,
  prop,
  isRequired,
  depth,
}: {
  name: string;
  prop: JsonSchemaProperty;
  isRequired: boolean;
  depth: number;
}) {
  const [expanded, setExpanded] = useState(depth < 1);
  const hasChildren =
    (prop.type === "object" && prop.properties && Object.keys(prop.properties).length > 0) ||
    (prop.type === "array" && prop.items?.type === "object" && prop.items?.properties);

  const nestedProperties =
    prop.type === "object"
      ? prop.properties
      : prop.type === "array" && prop.items?.type === "object"
        ? prop.items.properties
        : undefined;

  const nestedRequired =
    prop.type === "object"
      ? prop.required
      : prop.type === "array" && prop.items?.type === "object"
        ? prop.items.required
        : undefined;

  return (
    <div>
      <div
        className={cn(
          "flex items-center gap-2 py-1.5 px-2 rounded-md transition-colors",
          hasChildren && "cursor-pointer hover:bg-muted/30"
        )}
        onClick={hasChildren ? () => setExpanded(!expanded) : undefined}
      >
        {hasChildren ? (
          expanded ? (
            <ChevronDown className="size-3 shrink-0 text-muted-foreground" />
          ) : (
            <ChevronRight className="size-3 shrink-0 text-muted-foreground" />
          )
        ) : (
          <span className="w-3 shrink-0" />
        )}

        <span className="text-sm font-medium">{name}</span>
        {isRequired && (
          <span className="text-[10px] text-destructive font-medium">*</span>
        )}

        <Badge
          variant="outline"
          className={cn(
            "text-[10px] ml-1",
            TYPE_COLORS[prop.type ?? ""] ?? "bg-muted text-muted-foreground border-border"
          )}
        >
          {getTypeLabel(prop)}
        </Badge>

        {prop.enum && (
          <span className="text-[10px] text-muted-foreground ml-1">
            [{prop.enum.join(" | ")}]
          </span>
        )}

        {prop.default !== undefined && (
          <span className="text-[10px] text-muted-foreground ml-auto">
            default: {JSON.stringify(prop.default)}
          </span>
        )}
      </div>

      {prop.description && (
        <p className="text-xs text-muted-foreground ml-7 -mt-0.5 mb-1">
          {prop.description}
        </p>
      )}

      {expanded && hasChildren && nestedProperties && (
        <div className="ml-4 border-l border-border/50 pl-2">
          {Object.entries(nestedProperties).map(([childName, childProp]) => (
            <PropertyRow
              key={childName}
              name={childName}
              prop={childProp}
              isRequired={nestedRequired?.includes(childName) ?? false}
              depth={depth + 1}
            />
          ))}
        </div>
      )}
    </div>
  );
}

export function SchemaViewer({ schema }: { schema: Record<string, unknown> }) {
  const typedSchema = schema as JsonSchema;

  if (!typedSchema.properties || Object.keys(typedSchema.properties).length === 0) {
    if (Object.keys(schema).length === 0) {
      return (
        <p className="text-xs text-muted-foreground italic">No schema defined</p>
      );
    }
    // Fallback: render raw JSON for non-standard schemas
    return (
      <pre className="overflow-x-auto rounded-md bg-muted/50 p-3 font-mono text-xs">
        {JSON.stringify(schema, null, 2)}
      </pre>
    );
  }

  return (
    <div className="space-y-0.5">
      {typedSchema.description && (
        <p className="text-xs text-muted-foreground mb-3">{typedSchema.description}</p>
      )}
      {Object.entries(typedSchema.properties).map(([name, prop]) => (
        <PropertyRow
          key={name}
          name={name}
          prop={prop}
          isRequired={typedSchema.required?.includes(name) ?? false}
          depth={0}
        />
      ))}
    </div>
  );
}
