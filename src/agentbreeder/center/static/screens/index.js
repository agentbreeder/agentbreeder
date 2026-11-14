// Routes and the sidebar: one line per screen. A screen appears in the sidebar only when it works.
// `scoped` screens need one project; under "All projects" the shell asks which.

import { Audit } from "./audit.js";
import { Deployments } from "./deployments.js";
import { Home } from "./home.js";
import { Kubernetes } from "./kubernetes.js";
import { REGISTRY_ROUTES } from "./registry.js";
import { Release } from "./release.js";
import { Services } from "./services.js";

export const ROUTES = [
  { pattern: "/", title: "Home", nav: "Home", screen: Home },
  { pattern: "/services", title: "Services", nav: "Services", scoped: true, screen: Services },
  { pattern: "/release", title: "Build and release", nav: "Build and release", scoped: true, screen: Release },
  { pattern: "/deployments", title: "Deployments", nav: "Deployments", scoped: true, screen: Deployments },
  ...REGISTRY_ROUTES,
  { pattern: "/targets/kubernetes", title: "Kubernetes", nav: "Kubernetes", group: "Targets", scoped: true, screen: Kubernetes },
  { pattern: "/audit", title: "Audit", nav: "Audit", group: "Governance", scoped: true, screen: Audit },
];
