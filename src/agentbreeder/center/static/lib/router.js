// Hash routes (`#/agents/<id>/try`). Pure functions: the shell owns `location` and the DOM.
//
// A `#t=<token>` fragment is the one-time sign-in link from the terminal. It is consumed before
// any route is read, and the shell then replaces it with the route it came for, so the token
// never stays in the address bar or the history.

const TOKEN = /^#t=([A-Za-z0-9_-]{16,256})$/;
// One segment; never "." or "..", which a URL parser would collapse into a different path.
const PARAM = /^(?!\.{1,2}$)[A-Za-z0-9._-]{1,128}$/;
export const SUFFIX = "AgentBreeder console";

/** The one-time token in a `#t=` fragment, or null. */
export function tokenFrom(hash) {
  const match = TOKEN.exec(hash || "");
  return match ? match[1] : null;
}

/** The route path of a hash: `#/agents/x` gives `/agents/x`; anything else gives `/`. */
export function pathFrom(hash) {
  if (!hash || !hash.startsWith("#/")) return "/";
  const path = hash.slice(1).split("?")[0];
  return path.length > 1 && path.endsWith("/") ? path.slice(0, -1) : path;
}

function split(path) {
  return path.split("/").filter(Boolean);
}

/**
 * A router over `[{pattern, title, ...}]`. Patterns are `/`, `/audit` or `/agents/:id/try`.
 * A parameter is one segment of letters, digits, dot, underscore or dash; anything else does not
 * match, so a parameter is never a path or a URL.
 */
export function createRouter(routes) {
  const compiled = routes.map((route) => ({ route, parts: split(route.pattern) }));
  return {
    match(path) {
      const parts = split(path);
      for (const { route, parts: pattern } of compiled) {
        if (pattern.length !== parts.length) continue;
        const params = {};
        let ok = true;
        for (let i = 0; i < pattern.length && ok; i += 1) {
          if (pattern[i].startsWith(":")) {
            let value;
            try {
              value = decodeURIComponent(parts[i]);
            } catch {
              value = "";
            }
            ok = PARAM.test(value);
            params[pattern[i].slice(1)] = value;
          } else {
            ok = pattern[i] === parts[i];
          }
        }
        if (ok) return { route, params };
      }
      return null;
    },
  };
}

/** The hash for a pattern and its parameters: href("/agents/:id", {id: "a"}) gives `#/agents/a`. */
export function href(pattern, params = {}) {
  const path = split(pattern)
    .map((part) => {
      if (!part.startsWith(":")) return part;
      const value = String(params[part.slice(1)] ?? "");
      if (!PARAM.test(value)) throw new Error(`bad route parameter: ${part}`);
      return encodeURIComponent(value);
    })
    .join("/");
  return `#/${path}`;
}

/** A route's title: a string, or a function of the route's parameters (a detail page's name). */
export function routeTitle(route, params = {}) {
  if (!route || !route.title) return "";
  return typeof route.title === "function" ? String(route.title(params)) : route.title;
}

/** The document title for a route: "Audit · AgentBreeder console". */
export function titleFor(route, params = {}) {
  const title = routeTitle(route, params);
  return title ? `${title} · ${SUFFIX}` : SUFFIX;
}
