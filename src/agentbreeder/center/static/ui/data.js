// Reading a view: `useView(path)` gives {loading, data, error, stale} and a reload function. A
// view reads again when its project's version changes (a job in it ended) and on reload().

import { useConsole } from "./components.js";
import { useEffect, useState } from "./preact.js";

export function useView(path, project) {
  const { client, versionOf } = useConsole();
  const version = project ? versionOf(project) : 0;
  const [tick, setTick] = useState(0);
  const [view, setView] = useState({ loading: true, data: null, error: null, stale: false });
  useEffect(() => {
    if (!path) return undefined;
    let live = true;
    setView((previous) => ({ ...previous, loading: true }));
    client
      .request(path)
      .then((reply) => live && setView({ loading: false, data: reply.data, error: reply.error, stale: reply.stale }))
      .catch((error) => {
        if (!live || error.kind === "session" || error.kind === "unreachable") return;
        setView((previous) => ({ loading: false, data: previous.data, error: { code: error.code, message: error.message }, stale: Boolean(previous.data) }));
      });
    return () => {
      live = false;
    };
  }, [path, version, tick]);
  return [view, () => setTick((n) => n + 1)];
}
