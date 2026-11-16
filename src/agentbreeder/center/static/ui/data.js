// Reading a view: `useView(path)` gives {loading, data, error, stale, path} and a reload function
// (`path`: the read `data` came from, so a caller can tell another path's data from its own). A
// view reads again when its project's version changes (a job in it ended) and on reload(). A view
// of no project reads again when `version` changes (the Agents list passes `jobsEnded()`, #1073).

import { useConsole } from "./components.js";
import { useEffect, useState } from "./preact.js";

export function useView(path, project, other = 0) {
  const { client, versionOf } = useConsole();
  const version = project ? versionOf(project) : other;
  const [tick, setTick] = useState(0);
  const [view, setView] = useState({ loading: true, data: null, error: null, stale: false, path: null });
  useEffect(() => {
    if (!path) return undefined;
    let live = true;
    setView((previous) => ({ ...previous, loading: true }));
    client
      .request(path)
      .then((reply) => live && setView({ loading: false, data: reply.data, error: reply.error, stale: reply.stale, path }))
      .catch((error) => {
        if (!live || error.kind === "session" || error.kind === "unreachable") return;
        setView((previous) => ({ loading: false, data: previous.data, error: { code: error.code, message: error.message }, stale: Boolean(previous.data), path: previous.path }));
      });
    return () => {
      live = false;
    };
  }, [path, version, tick]);
  return [view, () => setTick((n) => n + 1)];
}
