/**
 * Prompt YAML serialization/deserialization.
 *
 * Converts prompt objects to/from a human-readable YAML format
 * suitable for version control and the AgentHub registry.
 */

export interface PromptVariable {
  name: string;
  description: string;
  default: string;
}

export interface PromptYamlData {
  name: string;
  version: string;
  description: string;
  tags: string[];
  variables: PromptVariable[];
  content: string;
}

/** Extract template variables ({{var_name}}) from prompt content. */
export function extractVariables(content: string): string[] {
  const regex = /\{\{(\w+)\}\}/g;
  const vars = new Set<string>();
  let match: RegExpExecArray | null;
  while ((match = regex.exec(content)) !== null) {
    vars.add(match[1]);
  }
  return [...vars];
}

/** Convert a prompt object to YAML string. */
export function promptToYaml(data: PromptYamlData): string {
  const lines: string[] = [];

  lines.push(`name: ${data.name}`);
  lines.push(`version: "${data.version}"`);

  if (data.description) {
    lines.push(`description: "${escapeYamlString(data.description)}"`);
  }

  if (data.tags.length > 0) {
    lines.push(`tags: [${data.tags.join(", ")}]`);
  }

  if (data.variables.length > 0) {
    lines.push("variables:");
    for (const v of data.variables) {
      lines.push(`  - name: ${v.name}`);
      if (v.description) {
        lines.push(`    description: "${escapeYamlString(v.description)}"`);
      }
      if (v.default) {
        lines.push(`    default: "${escapeYamlString(v.default)}"`);
      }
    }
  }

  lines.push("content: |");
  const contentLines = data.content.split("\n");
  for (const line of contentLines) {
    lines.push(`  ${line}`);
  }

  return lines.join("\n") + "\n";
}

/** Parse a YAML string back into a prompt object. */
export function yamlToPrompt(yaml: string): PromptYamlData {
  const result: PromptYamlData = {
    name: "",
    version: "1.0.0",
    description: "",
    tags: [],
    variables: [],
    content: "",
  };

  const lines = yaml.split("\n");
  let i = 0;

  while (i < lines.length) {
    const line = lines[i];

    // name
    const nameMatch = line.match(/^name:\s*(.+)$/);
    if (nameMatch) {
      result.name = unquote(nameMatch[1].trim());
      i++;
      continue;
    }

    // version
    const versionMatch = line.match(/^version:\s*(.+)$/);
    if (versionMatch) {
      result.version = unquote(versionMatch[1].trim());
      i++;
      continue;
    }

    // description
    const descMatch = line.match(/^description:\s*(.+)$/);
    if (descMatch) {
      result.description = unquote(descMatch[1].trim());
      i++;
      continue;
    }

    // tags
    const tagsMatch = line.match(/^tags:\s*\[(.+)\]$/);
    if (tagsMatch) {
      result.tags = tagsMatch[1].split(",").map((t) => t.trim()).filter(Boolean);
      i++;
      continue;
    }

    // variables block
    if (line.match(/^variables:\s*$/)) {
      i++;
      while (i < lines.length && lines[i].match(/^\s{2}-\s+name:\s+/)) {
        const varNameMatch = lines[i].match(/^\s{2}-\s+name:\s+(.+)$/);
        const variable: PromptVariable = {
          name: varNameMatch ? varNameMatch[1].trim() : "",
          description: "",
          default: "",
        };
        i++;
        // Read optional description and default
        while (i < lines.length && lines[i].match(/^\s{4}\w/)) {
          const descVarMatch = lines[i].match(/^\s{4}description:\s*(.+)$/);
          if (descVarMatch) {
            variable.description = unquote(descVarMatch[1].trim());
          }
          const defaultMatch = lines[i].match(/^\s{4}default:\s*(.+)$/);
          if (defaultMatch) {
            variable.default = unquote(defaultMatch[1].trim());
          }
          i++;
        }
        result.variables.push(variable);
      }
      continue;
    }

    // content block (literal block scalar)
    if (line.match(/^content:\s*\|\s*$/)) {
      i++;
      const contentLines: string[] = [];
      while (i < lines.length) {
        const contentLine = lines[i];
        // Content lines are indented by 2 spaces in YAML literal blocks
        if (contentLine.match(/^\s{2}/) || contentLine.trim() === "") {
          contentLines.push(contentLine.replace(/^\s{2}/, ""));
        } else {
          break;
        }
        i++;
      }
      // Remove trailing empty line that YAML adds
      while (contentLines.length > 0 && contentLines[contentLines.length - 1] === "") {
        contentLines.pop();
      }
      result.content = contentLines.join("\n");
      continue;
    }

    i++;
  }

  return result;
}

function escapeYamlString(s: string): string {
  return s.replace(/\\/g, "\\\\").replace(/"/g, '\\"');
}

function unquote(s: string): string {
  if ((s.startsWith('"') && s.endsWith('"')) || (s.startsWith("'") && s.endsWith("'"))) {
    return s.slice(1, -1).replace(/\\"/g, '"').replace(/\\\\/g, "\\");
  }
  return s;
}
