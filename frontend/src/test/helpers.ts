import { vi } from "vitest";
import violation from "../mock/fixtures/ui-report-violation.json";
import juryViolation from "../mock/fixtures/jury-violation.json";

export const JOB = "00000000-0000-4000-8000-000000000001";
export const KEY = "s3cret-jury-key-XYZ";

/** Controllable XMLHttpRequest stand-in (upload uses XHR for byte progress). */
export class FakeXHR {
  static instances: FakeXHR[] = [];
  method = "";
  url = "";
  headers: Record<string, string> = {};
  body: FormData | null = null;
  status = 0;
  responseText = "";
  respHeaders: Record<string, string> = {};
  upload: { onprogress: ((ev: ProgressEvent) => void) | null } = { onprogress: null };
  onload: (() => void) | null = null;
  onerror: (() => void) | null = null;
  onabort: (() => void) | null = null;
  constructor() {
    FakeXHR.instances.push(this);
  }
  open(method: string, url: string) {
    this.method = method;
    this.url = url;
  }
  setRequestHeader(k: string, v: string) {
    this.headers[k] = v;
  }
  getResponseHeader(k: string) {
    return this.respHeaders[k.toLowerCase()] ?? null;
  }
  send(body: FormData) {
    this.body = body;
  }
  abort() {
    this.onabort?.();
  }
  progress(loaded: number, total: number) {
    this.upload.onprogress?.({ lengthComputable: true, loaded, total } as ProgressEvent);
  }
  respond(status: number, body: unknown, headers: Record<string, string> = {}) {
    this.status = status;
    this.responseText = typeof body === "string" ? body : JSON.stringify(body);
    this.respHeaders = Object.fromEntries(Object.entries(headers).map(([k, v]) => [k.toLowerCase(), v]));
    this.onload?.();
  }
}

export function installXHR() {
  FakeXHR.instances = [];
  vi.stubGlobal("XMLHttpRequest", FakeXHR);
}

export const json = (body: unknown, status = 200, headers: Record<string, string> = {}) =>
  new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json", ...headers } });

export type Step = Response | "network" | (() => Response | "network");

/**
 * Fetch router: `jobs` and `report` are consumed sequentially (last one repeats).
 * Every call is recorded with its URL and X-API-Key header.
 */
export function installFetch(opts: {
  jobs: Step[];
  report?: Step[];
  media?: () => Response;
  /** Checked first; return undefined to fall through to the default routing. */
  extra?: (url: string, init?: RequestInit) => Response | undefined;
}) {
  const calls: { url: string; key: string | null; method: string; body: string | null }[] = [];
  let ji = 0;
  let ri = 0;
  const take = (arr: Step[], i: number): Response | "network" => {
    const s = arr[Math.min(i, arr.length - 1)];
    const v = typeof s === "function" ? s() : s;
    return v instanceof Response ? v.clone() : v;
  };
  const fn = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    const key = new Headers(init?.headers).get("X-API-Key");
    calls.push({ url, key, method: (init?.method ?? "GET").toUpperCase(), body: (init?.body as string | undefined) ?? null });
    let r: Response | "network";
    const ex = opts.extra?.(url, init);
    if (ex) r = ex;
    else if (url.endsWith("/report")) r = take(opts.report ?? [json(violation)], ri++);
    else if (url.endsWith("/media")) r = opts.media ? opts.media() : new Response("", { status: 404 });
    else if (url.includes("/evidence/")) r = new Response(new Blob([new Uint8Array([0xff, 0xd8, 0xff])], { type: "image/jpeg" }));
    else r = take(opts.jobs, ji++);
    if (r === "network") throw new TypeError("Failed to fetch");
    return r;
  });
  vi.stubGlobal("fetch", fn);
  return { fn, calls, jobCalls: () => calls.filter((c) => /\/jobs\/[^/]+$/.test(c.url)).length };
}

export const processing = () => json({ job_id: JOB, status: "processing" });
export const juryDone = () => json(juryViolation);
export const violationReport = violation;

export const movFile = (name = "clip.mov", size = 1000) =>
  new File([new Uint8Array(size)], name, { type: "video/quicktime" });
