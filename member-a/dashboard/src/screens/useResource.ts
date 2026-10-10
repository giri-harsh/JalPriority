import { useEffect, useRef, useState } from "react";

export type Resource<T> =
  | { status: "idle" }
  | { status: "loading" }
  | { status: "ready"; data: T }
  | { status: "error"; error: unknown };

const IDLE: Resource<never> = { status: "idle" };
const LOADING: Resource<never> = { status: "loading" };

interface Slot<T> {
  key: string | null;
  resource: Resource<T>;
}

/**
 * Loads one resource per `key` and returns its state.
 *
 * A `null` key means "nothing to load" (idle). When the key changes the previous
 * data is never returned: the hook reports `loading` until the new request settles.
 * `deps` re-runs the request (for example after a token change) without discarding data.
 */
export function useResource<T>(
  key: string | null,
  load: (signal: AbortSignal) => Promise<T>,
  deps: readonly unknown[],
): Resource<T> {
  const [slot, setSlot] = useState<Slot<T>>({ key: null, resource: IDLE });
  const loadRef = useRef(load);
  loadRef.current = load;

  useEffect(() => {
    if (key === null) {
      setSlot({ key: null, resource: IDLE });
      return undefined;
    }
    const controller = new AbortController();
    setSlot((previous) => (previous.key === key ? previous : { key, resource: LOADING }));
    loadRef.current(controller.signal).then(
      (data) => {
        if (!controller.signal.aborted) setSlot({ key, resource: { status: "ready", data } });
      },
      (error: unknown) => {
        if (!controller.signal.aborted) setSlot({ key, resource: { status: "error", error } });
      },
    );
    return () => controller.abort();
    // `deps` is a caller-provided list of extra triggers with a stable length.
  }, [key, ...deps]);

  if (key === null) return IDLE;
  return slot.key === key ? slot.resource : LOADING;
}
