/**
 * Agent YAML emit + parse helpers — shared by the agent builder.
 *
 * Extracted from `pages/agent-builder.tsx` so we can:
 *  1. round-trip language + gateways without losing data
 *  2. unit-test the round-trip independently of the UI
 *
 * IMPORTANT: the parser at `engine/config_parser.py` rejects a top-level
 * `language:` key. The polyglot contract is:
 *   - python  →  emit `framework: <fw>`        (no runtime block)
 *   - other   →  emit `runtime: { language: <lang>, framework: <fw> }`
 *
 * `gateways:` is a top-level dict of GatewayConfig overrides — see #164 and
 * `engine/providers/catalog.yaml` for context.
 */

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export type AgentLanguage = "python" | "typescript";

export interface GatewayOverride {
  /** Override the catalog base_url for this gateway. */
  url?: string;
  /** Override the env var the gateway reads its api key from. */
  api_key_env?: string;
  /** Advisory routing hint — "fastest" | "cheapest" | "first". */
  fallback_policy?: "fastest" | "cheapest" | "first";
  /** Default headers to attach to every gateway request. */
  default_headers?: Record<string, string>;
}

export interface AgentFormData {
  name: string;
  version: string;
  description: string;
  team: string;
  owner: string;
  tags: string[];
  /** Track I — UI concept; emitted under `runtime:` for non-python. */
  language: AgentLanguage;
  model: {
    primary: string;
    fallback: string;
    temperature: number;
    max_tokens: number;
  };
  framework: string;
  tools: string[];
  prompts: {
    system: string;
  };
  guardrails: string[];
  /** Track H — top-level `gateways:` block (per-gateway overrides). */
  gateways: Record<string, GatewayOverride>;
  deploy: {
    cloud: string;
    runtime: string;
    scalingMin: number;
    scalingMax: number;
  };
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

/** TypeScript runs on the Node runtime, so `runtime.language` is "node". */
const TYPESCRIPT_RUNTIME_LANG = "node";

function mapUiLanguageToRuntime(lang: AgentLanguage): string {
  return lang === "typescript" ? TYPESCRIPT_RUNTIME_LANG : "python";
}

function mapRuntimeToUiLanguage(runtimeLang: string): AgentLanguage {
  // node + typescript both come back as "typescript" in the UI; everything
  // else (including go/rust/etc.) collapses to python because the visual
  // builder only exposes the python/typescript toggle today.
  return runtimeLang === "node" || runtimeLang === "typescript" ? "typescript" : "python";
}

// ---------------------------------------------------------------------------
// Emit: AgentFormData → YAML string
// ---------------------------------------------------------------------------

export function formDataToYaml(data: AgentFormData): string {
  const lines: string[] = [];
  lines.push(`name: ${data.name || "my-agent"}`);
  lines.push(`version: "${data.version || "0.1.0"}"`);
  lines.push(`description: "${data.description}"`);
  lines.push(`team: ${data.team || "engineering"}`);
  lines.push(`owner: ${data.owner || "user@example.com"}`);

  if (data.tags.length > 0) {
    lines.push(`tags: [${data.tags.join(", ")}]`);
  }

  lines.push("");
  lines.push("model:");
  lines.push(`  primary: ${data.model.primary || "claude-sonnet-4"}`);
  if (data.model.fallback) {
    lines.push(`  fallback: ${data.model.fallback}`);
  }
  lines.push(`  temperature: ${data.model.temperature}`);
  lines.push(`  max_tokens: ${data.model.max_tokens}`);

  lines.push("");
  // Polyglot contract: python → framework:, other → runtime:{language,framework}.
  if (data.language === "typescript") {
    lines.push("runtime:");
    lines.push(`  language: ${mapUiLanguageToRuntime(data.language)}`);
    lines.push(`  framework: ${data.framework || "custom"}`);
  } else {
    lines.push(`framework: ${data.framework || "langgraph"}`);
  }

  lines.push("");
  if (data.tools.length === 0) {
    lines.push("tools: []");
  } else {
    lines.push("tools:");
    for (const tool of data.tools) {
      lines.push(`  - ref: ${tool}`);
    }
  }

  lines.push("");
  lines.push("prompts:");
  if (data.prompts.system.startsWith("prompts/")) {
    lines.push(`  system: ${data.prompts.system}`);
  } else {
    lines.push(`  system: "${data.prompts.system.replace(/"/g, '\\"')}"`);
  }

  lines.push("");
  if (data.guardrails.length === 0) {
    lines.push("guardrails: []");
  } else {
    lines.push("guardrails:");
    for (const g of data.guardrails) {
      lines.push(`  - ${g}`);
    }
  }

  // Top-level gateways: only emitted if at least one gateway has overrides.
  const gatewayEntries = Object.entries(data.gateways).filter(([, override]) => {
    if (!override) return false;
    return (
      override.url ||
      override.api_key_env ||
      override.fallback_policy ||
      (override.default_headers && Object.keys(override.default_headers).length > 0)
    );
  });
  if (gatewayEntries.length > 0) {
    lines.push("");
    lines.push("gateways:");
    for (const [name, override] of gatewayEntries) {
      lines.push(`  ${name}:`);
      if (override.url) lines.push(`    url: ${override.url}`);
      if (override.api_key_env) lines.push(`    api_key_env: ${override.api_key_env}`);
      if (override.fallback_policy) lines.push(`    fallback_policy: ${override.fallback_policy}`);
      if (override.default_headers && Object.keys(override.default_headers).length > 0) {
        lines.push(`    default_headers:`);
        for (const [hk, hv] of Object.entries(override.default_headers)) {
          lines.push(`      ${hk}: "${hv}"`);
        }
      }
    }
  }

  lines.push("");
  lines.push("deploy:");
  lines.push(`  cloud: ${data.deploy.cloud}`);
  lines.push(`  runtime: ${data.deploy.runtime}`);
  lines.push("  scaling:");
  lines.push(`    min: ${data.deploy.scalingMin}`);
  lines.push(`    max: ${data.deploy.scalingMax}`);

  return lines.join("\n");
}

// ---------------------------------------------------------------------------
// Parse: YAML string → AgentFormData
// ---------------------------------------------------------------------------

export function emptyFormData(): AgentFormData {
  return {
    name: "",
    version: "0.1.0",
    description: "",
    team: "engineering",
    owner: "user@example.com",
    tags: [],
    language: "python",
    model: { primary: "claude-sonnet-4", fallback: "", temperature: 0.7, max_tokens: 4096 },
    framework: "langgraph",
    tools: [],
    prompts: { system: "" },
    guardrails: [],
    gateways: {},
    deploy: { cloud: "local", runtime: "docker-compose", scalingMin: 1, scalingMax: 10 },
  };
}

export function yamlToFormData(yaml: string): AgentFormData {
  const data = emptyFormData();

  const lines = yaml.split("\n");
  let currentSection = "";
  // For nested 2-deep blocks like gateways: <name>: ...
  let currentGatewayName: string | null = null;
  let currentHeaderBlock = false;

  for (const rawLine of lines) {
    const line = rawLine.trimEnd();
    const trimmed = line.trim();
    if (!trimmed || trimmed.startsWith("#")) continue;

    const leadingSpaces = line.length - line.trimStart().length;

    // Top-level fields (no leading whitespace)
    if (leadingSpaces === 0) {
      const colonIdx = trimmed.indexOf(":");
      if (colonIdx === -1) continue;
      const key = trimmed.slice(0, colonIdx).trim();
      const val = trimmed.slice(colonIdx + 1).trim().replace(/^"|"$/g, "");
      currentSection = key;
      currentGatewayName = null;
      currentHeaderBlock = false;

      switch (key) {
        case "name":
          data.name = val;
          break;
        case "version":
          data.version = val;
          break;
        case "description":
          data.description = val;
          break;
        case "team":
          data.team = val;
          break;
        case "owner":
          data.owner = val;
          break;
        case "framework":
          // Top-level framework => python language.
          data.framework = val;
          data.language = "python";
          break;
        case "tags": {
          const tagMatch = val.match(/\[([^\]]*)\]/);
          if (tagMatch) {
            data.tags = tagMatch[1].split(",").map((t) => t.trim()).filter(Boolean);
          }
          break;
        }
        case "tools":
          if (val === "[]") data.tools = [];
          break;
        case "guardrails":
          if (val === "[]") data.guardrails = [];
          break;
        case "gateways":
          // Inline empty mapping
          if (val === "{}") data.gateways = {};
          break;
      }
    } else {
      // Nested fields
      const colonIdx = trimmed.indexOf(":");

      // Array item like "- ref: xxx" or "- pii_detection"
      if (trimmed.startsWith("- ")) {
        const itemVal = trimmed.slice(2).trim();
        if (currentSection === "tools") {
          const refMatch = itemVal.match(/^ref:\s*(.+)$/);
          if (refMatch) data.tools.push(refMatch[1].trim());
        } else if (currentSection === "guardrails") {
          data.guardrails.push(itemVal);
        }
        continue;
      }

      if (colonIdx === -1) continue;

      const key = trimmed.slice(0, colonIdx).trim();
      const val = trimmed.slice(colonIdx + 1).trim().replace(/^"|"$/g, "");

      if (currentSection === "model") {
        switch (key) {
          case "primary":
            data.model.primary = val;
            break;
          case "fallback":
            data.model.fallback = val;
            break;
          case "temperature":
            data.model.temperature = parseFloat(val) || 0.7;
            break;
          case "max_tokens":
            data.model.max_tokens = parseInt(val) || 4096;
            break;
        }
      } else if (currentSection === "runtime") {
        // runtime: { language, framework, version, entrypoint }
        if (key === "language") {
          data.language = mapRuntimeToUiLanguage(val);
        } else if (key === "framework") {
          data.framework = val;
        }
      } else if (currentSection === "prompts") {
        if (key === "system") data.prompts.system = val;
      } else if (currentSection === "deploy") {
        switch (key) {
          case "cloud":
            data.deploy.cloud = val;
            break;
          case "runtime":
            data.deploy.runtime = val;
            break;
          case "min":
            data.deploy.scalingMin = parseInt(val) || 1;
            break;
          case "max":
            data.deploy.scalingMax = parseInt(val) || 10;
            break;
        }
      } else if (currentSection === "gateways") {
        // 2-space indent => gateway name; 4-space indent => override fields;
        // 6-space indent => default_headers entries.
        if (leadingSpaces === 2 && val === "") {
          currentGatewayName = key;
          currentHeaderBlock = false;
          if (!data.gateways[currentGatewayName]) {
            data.gateways[currentGatewayName] = {};
          }
        } else if (leadingSpaces === 4 && currentGatewayName) {
          const gw = data.gateways[currentGatewayName] ?? {};
          if (key === "url") gw.url = val;
          else if (key === "api_key_env") gw.api_key_env = val;
          else if (key === "fallback_policy") {
            if (val === "fastest" || val === "cheapest" || val === "first") {
              gw.fallback_policy = val;
            }
          } else if (key === "default_headers") {
            currentHeaderBlock = true;
            gw.default_headers = gw.default_headers ?? {};
          }
          data.gateways[currentGatewayName] = gw;
        } else if (leadingSpaces === 6 && currentGatewayName && currentHeaderBlock) {
          const gw = data.gateways[currentGatewayName] ?? {};
          gw.default_headers = gw.default_headers ?? {};
          gw.default_headers[key] = val;
          data.gateways[currentGatewayName] = gw;
        }
      }
    }
  }

  return data;
}

// ---------------------------------------------------------------------------
// Round-trip helper — used by tests.
// ---------------------------------------------------------------------------

export function roundTripFormData(data: AgentFormData): AgentFormData {
  return yamlToFormData(formDataToYaml(data));
}
