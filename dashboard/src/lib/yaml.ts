/**
 * JSON-to-YAML converter and YAML syntax highlighter.
 * No external dependencies — hand-rolled for AgentBreeder.
 */

// ---------------------------------------------------------------------------
// JSON → YAML conversion
// ---------------------------------------------------------------------------

function needsQuoting(value: string): boolean {
  if (value === "") return true;
  // Strings that look like booleans, nulls, or numbers
  if (/^(true|false|yes|no|on|off|null|~)$/i.test(value)) return true;
  if (/^[\d.+-]+$/.test(value) && !isNaN(Number(value))) return true;
  // Contains special YAML characters
  if (/[:#{}[\],&*?|>!%@`]/.test(value)) return true;
  // Starts or ends with whitespace
  if (value !== value.trim()) return true;
  return false;
}

function formatValue(value: unknown): string {
  if (value === null || value === undefined) return "null";
  if (typeof value === "boolean") return value ? "true" : "false";
  if (typeof value === "number") return String(value);
  if (typeof value === "string") {
    // Multi-line strings are handled separately in jsonToYamlLines so they
    // can be indented correctly relative to their parent key.
    return needsQuoting(value) ? `"${value.replace(/\\/g, "\\\\").replace(/"/g, '\\"')}"` : value;
  }
  return String(value);
}

function isMultiLine(value: unknown): value is string {
  return typeof value === "string" && value.includes("\n");
}

function jsonToYamlLines(obj: unknown, indent: number = 0): string[] {
  const prefix = "  ".repeat(indent);
  const lines: string[] = [];

  if (obj === null || obj === undefined) {
    lines.push(`${prefix}null`);
    return lines;
  }

  if (typeof obj !== "object") {
    lines.push(`${prefix}${formatValue(obj)}`);
    return lines;
  }

  if (Array.isArray(obj)) {
    if (obj.length === 0) {
      lines.push(`${prefix}[]`);
      return lines;
    }
    for (const item of obj) {
      if (item !== null && typeof item === "object" && !Array.isArray(item)) {
        const entries = Object.entries(item as Record<string, unknown>);
        if (entries.length > 0) {
          const [firstKey, firstVal] = entries[0];
          if (firstVal !== null && typeof firstVal === "object") {
            lines.push(`${prefix}- ${firstKey}:`);
            lines.push(...jsonToYamlLines(firstVal, indent + 2));
          } else {
            lines.push(`${prefix}- ${firstKey}: ${formatValue(firstVal)}`);
          }
          for (const [key, val] of entries.slice(1)) {
            if (val !== null && typeof val === "object") {
              lines.push(`${prefix}  ${key}:`);
              lines.push(...jsonToYamlLines(val, indent + 2));
            } else {
              lines.push(`${prefix}  ${key}: ${formatValue(val)}`);
            }
          }
        } else {
          lines.push(`${prefix}- {}`);
        }
      } else {
        lines.push(`${prefix}- ${formatValue(item)}`);
      }
    }
    return lines;
  }

  // Object
  const entries = Object.entries(obj as Record<string, unknown>);
  if (entries.length === 0) {
    lines.push(`${prefix}{}`);
    return lines;
  }

  for (const [key, val] of entries) {
    if (isMultiLine(val)) {
      // Literal block scalar: indent content one level deeper than the key
      const contentPrefix = "  ".repeat(indent + 1);
      lines.push(`${prefix}${key}: |`);
      for (const contentLine of val.split("\n")) {
        // Preserve empty lines in the block but don't add spurious trailing spaces
        lines.push(contentLine === "" ? "" : `${contentPrefix}${contentLine}`);
      }
    } else if (val !== null && typeof val === "object") {
      if (Array.isArray(val) && val.length === 0) {
        lines.push(`${prefix}${key}: []`);
      } else if (!Array.isArray(val) && Object.keys(val as Record<string, unknown>).length === 0) {
        lines.push(`${prefix}${key}: {}`);
      } else {
        lines.push(`${prefix}${key}:`);
        lines.push(...jsonToYamlLines(val, indent + 1));
      }
    } else {
      lines.push(`${prefix}${key}: ${formatValue(val)}`);
    }
  }

  return lines;
}

export function jsonToYaml(obj: unknown): string {
  return jsonToYamlLines(obj, 0).join("\n");
}

// ---------------------------------------------------------------------------
// YAML syntax highlighting → returns HTML string
// ---------------------------------------------------------------------------

function escapeHtml(str: string): string {
  return str
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

/**
 * Highlights a YAML string by wrapping tokens in <span> elements with
 * Tailwind dark/light classes.
 */
export function highlightYaml(yaml: string): string {
  const lines = yaml.split("\n");
  return lines
    .map((line) => {
      // Comment lines
      if (/^\s*#/.test(line)) {
        return `<span class="text-gray-500">${escapeHtml(line)}</span>`;
      }

      // Key-value lines: match leading whitespace + optional "- " + key: value
      const kvMatch = line.match(/^(\s*(?:-\s+)?)([^\s:][^:]*?)(:)(\s+(.*))?$/);
      if (kvMatch) {
        const [, leadingRaw, keyRaw, colonRaw, , valueRaw] = kvMatch;
        const leading = escapeHtml(leadingRaw || "");
        const key = escapeHtml(keyRaw);
        const colon = escapeHtml(colonRaw);
        let valuePart = "";
        if (valueRaw !== undefined) {
          valuePart = " " + highlightValue(valueRaw);
        }
        return `${leading}<span class="text-blue-600 dark:text-blue-400">${key}</span>${colon}${valuePart}`;
      }

      // Array items without key (e.g., "  - some_value")
      const arrMatch = line.match(/^(\s*-\s+)(.+)$/);
      if (arrMatch) {
        const [, prefix, value] = arrMatch;
        return escapeHtml(prefix) + highlightValue(value);
      }

      return escapeHtml(line);
    })
    .join("\n");
}

// ---------------------------------------------------------------------------
// Basic YAML validation (structural checks, no full parser)
// ---------------------------------------------------------------------------

export function validateYamlBasic(yaml: string): { valid: boolean; error: string | null } {
  if (!yaml.trim()) {
    return { valid: false, error: "Empty content" };
  }

  const lines = yaml.split("\n");
  let blockScalarIndent = -1; // indent level of a block scalar's content, -1 = not in one

  for (let i = 0; i < lines.length; i++) {
    const line = lines[i];

    // Skip empty lines
    if (!line.trim()) continue;

    const currentIndent = line.match(/^(\s*)/)?.[1].length ?? 0;

    // Detect end of block scalar: a non-empty line at or before the block's indent
    if (blockScalarIndent >= 0 && currentIndent <= blockScalarIndent) {
      blockScalarIndent = -1;
    }

    // Inside block scalar content — skip structural checks
    if (blockScalarIndent >= 0) continue;

    // Comments
    if (line.trim().startsWith("#")) continue;

    // Check for tabs (YAML uses spaces only)
    if (/^\t/.test(line)) {
      return { valid: false, error: `Line ${i + 1}: tabs are not allowed in YAML` };
    }

    // Detect start of a block scalar (key: | or key: >)
    if (/:\s*[|>][-+]?\s*$/.test(line)) {
      blockScalarIndent = currentIndent;
      continue;
    }

    // Check for unclosed quotes only on non-block-scalar lines
    const stripped = line.replace(/\\"/g, "").replace(/\\'/g, "");
    const doubleQuotes = (stripped.match(/"/g) || []).length;
    const singleQuotes = (stripped.match(/'/g) || []).length;
    if (doubleQuotes % 2 !== 0) {
      return { valid: false, error: `Line ${i + 1}: unclosed double quote` };
    }
    if (singleQuotes % 2 !== 0) {
      return { valid: false, error: `Line ${i + 1}: unclosed single quote` };
    }
  }

  return { valid: true, error: null };
}

function highlightValue(raw: string): string {
  const trimmed = raw.trim();

  // Null
  if (/^(null|~)$/i.test(trimmed)) {
    return `<span class="text-purple-600 dark:text-purple-400">${escapeHtml(raw)}</span>`;
  }

  // Booleans
  if (/^(true|false|yes|no|on|off)$/i.test(trimmed)) {
    return `<span class="text-purple-600 dark:text-purple-400">${escapeHtml(raw)}</span>`;
  }

  // Numbers
  if (/^-?[\d.]+$/.test(trimmed) && !isNaN(Number(trimmed))) {
    return `<span class="text-amber-600 dark:text-amber-400">${escapeHtml(raw)}</span>`;
  }

  // Quoted strings
  if (/^".*"$/.test(trimmed) || /^'.*'$/.test(trimmed)) {
    return `<span class="text-green-600 dark:text-green-400">${escapeHtml(raw)}</span>`;
  }

  // Empty collections
  if (trimmed === "[]" || trimmed === "{}") {
    return escapeHtml(raw);
  }

  // Block scalar indicators
  if (trimmed === "|" || trimmed === ">") {
    return escapeHtml(raw);
  }

  // Unquoted strings
  return `<span class="text-green-600 dark:text-green-400">${escapeHtml(raw)}</span>`;
}
