import { createContext, useContext, useEffect, useState } from "react";
import { fetchBlob } from "./api";

/**
 * Owns every object URL created for one displayed result (evidence frames,
 * preview video). revokeAll() is called when the result view unmounts/resets.
 */
export class BlobRegistry {
  private readonly key: string;
  private cache = new Map<string, Promise<string>>();
  private urls = new Set<string>();
  private controller = new AbortController();

  constructor(key: string) {
    this.key = key;
  }

  /** Authorized fetch -> object URL, deduplicated per API path. */
  get(path: string): Promise<string> {
    let p = this.cache.get(path);
    if (!p) {
      const signal = this.controller.signal;
      p = fetchBlob(path, this.key, signal).then((blob) => {
        if (signal.aborted) throw new Error("revoked");
        const url = URL.createObjectURL(blob);
        this.urls.add(url);
        return url;
      });
      const entry = p;
      entry.catch(() => {
        if (this.cache.get(path) === entry) this.cache.delete(path);
      });
      this.cache.set(path, entry);
    }
    return p;
  }

  revokeAll(): void {
    this.controller.abort();
    this.controller = new AbortController();
    for (const url of this.urls) URL.revokeObjectURL(url);
    this.urls.clear();
    this.cache.clear();
  }

  get size(): number {
    return this.urls.size;
  }
}

export const BlobRegistryContext = createContext<BlobRegistry | null>(null);

export type BlobState = { status: "loading" } | { status: "ok"; url: string } | { status: "error" };

export function useAuthorizedObjectUrl(path: string | null): BlobState {
  const registry = useContext(BlobRegistryContext);
  const [state, setState] = useState<BlobState>({ status: "loading" });
  useEffect(() => {
    if (!registry || !path) {
      setState({ status: "error" });
      return;
    }
    let active = true;
    setState({ status: "loading" });
    registry.get(path).then(
      (url) => active && setState({ status: "ok", url }),
      () => active && setState({ status: "error" }),
    );
    return () => {
      active = false;
    };
  }, [registry, path]);
  return state;
}
