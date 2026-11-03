import { useEffect, useState } from "react";
import { get, type Query } from "./api";

export type Loadable<T> = { data: T | null; error: string | null; loading: boolean };

/** Fetch `path` and refresh every `intervalMs` (daily-monitoring cadence by default). */
export function useApi<T>(path: string, query: Query = {}, intervalMs = 60_000): Loadable<T> {
  const [state, setState] = useState<Loadable<T>>({ data: null, error: null, loading: true });
  const key = `${path}?${JSON.stringify(query)}`;
  useEffect(() => {
    let alive = true;
    const load = () =>
      get<T>(path, query)
        .then((data) => alive && setState({ data, error: null, loading: false }))
        .catch((e: unknown) => alive && setState((s) => ({ ...s, error: String(e), loading: false })));
    void load();
    const id = window.setInterval(load, intervalMs);
    return () => {
      alive = false;
      window.clearInterval(id);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key, intervalMs]);
  return state;
}
