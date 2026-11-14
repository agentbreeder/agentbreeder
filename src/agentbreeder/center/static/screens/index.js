// Routes and the sidebar: one line per screen. A screen appears in the sidebar only when it works.
// `scoped` screens need one project; under "All projects" the shell asks which. A route without
// `nav` is not in the sidebar; `section` names the sidebar entry it belongs to, and moving between
// routes of one section keeps focus where it is (choosing a log source is not a new screen).

import { AGENT_ROUTES } from "./agents.js";
import { Audit } from "./audit.js";
import { Deployments } from "./deployments.js";
import { Home } from "./home.js";
import { Kubernetes } from "./kubernetes.js";
import { Logs } from "./logs.js";
import { Release } from "./release.js";
import { Services } from "./services.js";

export const ROUTES = [
  { pattern: "/", title: "Home", nav: "Home", screen: Home },
  ...AGENT_ROUTES,
  { pattern: "/services", title: "Services", nav: "Services", scoped: true, screen: Services },
  { pattern: "/release", title: "Build and release", nav: "Build and release", scoped: true, screen: Release },
  { pattern: "/deployments", title: "Deployments", nav: "Deployments", scoped: true, screen: Deployments },
  { pattern: "/logs", title: "Logs", nav: "Logs", scoped: true, screen: Logs },
  { pattern: "/logs/:kind/:name", title: "Logs", section: "/logs", scoped: true, screen: Logs },
  { pattern: "/targets/kubernetes", title: "Kubernetes", nav: "Kubernetes", group: "Targets", scoped: true, screen: Kubernetes },
  { pattern: "/audit", title: "Audit", nav: "Audit", group: "Governance", scoped: true, screen: Audit },
];
