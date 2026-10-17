// DEVELOPMENT-ONLY fixture layer, loaded exclusively by `npm run dev:fixtures`
// (vite --mode fixtures). It replaces fetch/XMLHttpRequest for /api/* with
// synthetic responses built from contracts/fixtures (copied to ./fixtures).
// Nothing here is real inference. The production build aliases this module to
// ./noop.ts, so none of this code or data reaches dist/.
//
// Scenario selection by uploaded file name (synthetic testing aid):
//   contains "clean" -> no_violation; "fail" -> failed; "busy" -> HTTP 429;
//   "flaky" -> transient 503 during polling; anything else -> two-event violation.
// Access key "bad" -> HTTP 401.

import failedReport from "./fixtures/ui-report-failed.json";
import cleanReport from "./fixtures/ui-report-no-violation.json";
import processingReport from "./fixtures/ui-report-processing.json";
import violationReport from "./fixtures/ui-report-violation.json";
import juryClean from "./fixtures/jury-no-violation.json";
import juryViolation from "./fixtures/jury-violation.json";
import jobsList from "./fixtures/jobs-list.json";
import e1Url from "./fixtures/e1.jpg?url";
import e2Url from "./fixtures/e2.jpg?url";

type Scenario = "violation" | "clean" | "fail" | "flaky";
interface MockJob {
  scenario: Scenario;
  polls: number;
}

const JOB_ID = violationReport.job_id;
const jobs = new Map<string, MockJob>();
const STAGES = ["probing", "sampling", "observing", "observing", "observing", "observing", "verifying", "rendering_evidence", "committing"] as const;
const realFetch = window.fetch.bind(window);
const startTimes = new Map<string, number>();
// Reviewer decisions per job (in-memory, echo of POST bodies; never alters the fixture jury result).
const reviewsByJob = new Map<string, Record<string, unknown>[]>();
let reviewSeq = 0;

// History demo: the listed jobs open instantly as already-finished fixtures.
for (const j of jobsList.jobs) {
  if (j.status === "processing") continue;
  jobs.set(j.job_id, { scenario: j.status === "failed" ? "fail" : "violation", polls: STAGES.length });
  startTimes.set(j.job_id, Date.now());
}

function reviewsOf(jobId: string, fixtureReviews: unknown[] = []): Record<string, unknown>[] {
  if (!reviewsByJob.has(jobId)) reviewsByJob.set(jobId, [...(fixtureReviews as Record<string, unknown>[])]);
  return reviewsByJob.get(jobId)!;
}

function terminalReport(job: MockJob, jobId: string) {
  if (job.scenario === "fail") return { ...failedReport, job_id: jobId };
  const base = job.scenario === "clean" ? cleanReport : violationReport;
  return { ...base, job_id: jobId, reviews: reviewsOf(jobId, base.reviews) };
}

function json(body: unknown, status = 200, headers: Record<string, string> = {}): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json", ...headers },
  });
}
const err = (status: number, code: string, message: string, headers: Record<string, string> = {}) =>
  json({ error: { code, message }, request_id: "fixture" }, status, headers);

function keyOf(init?: RequestInit): string | null {
  const h = new Headers(init?.headers);
  return h.get("X-API-Key");
}

function processingAt(job: MockJob, jobId: string) {
  const i = Math.min(job.polls, STAGES.length - 1);
  const stage = STAGES[i];
  const observingIdx = i - 2;
  const elapsed = (Date.now() - (startTimes.get(jobId) ?? Date.now())) / 1000;
  return {
    ...processingReport,
    job_id: jobId,
    stage,
    elapsed_sec: elapsed,
    progress: stage === "observing" ? { completed: Math.min(16, 4 * (observingIdx + 1)), total: 16, unit: "windows" } : null,
  };
}

async function mockFetch(input: RequestInfo | URL, init?: RequestInit): Promise<Response> {
  const url = typeof input === "string" ? input : input instanceof URL ? input.pathname : input.url;
  const full = url.startsWith("http") ? new URL(url).pathname + new URL(url).search : url;
  const [path, query = ""] = full.split("?");
  const method = (init?.method ?? "GET").toUpperCase();
  if (!path.startsWith("/api/")) return realFetch(input, init);
  await new Promise((r) => setTimeout(r, 120));
  const key = keyOf(init);
  if (!key || key === "bad") return err(401, "unauthorized", "Unauthorized");

  if (path === "/api/v1/jobs" && method === "GET") {
    const status = new URLSearchParams(query).get("status");
    const list = jobsList.jobs
      .map((j) => {
        const n = reviewsByJob.get(j.job_id);
        if (!n) return j;
        const reviewed = new Set(n.filter((r) => r.event_id !== "missed").map((r) => r.event_id)).size;
        return { ...j, reviewed_event_count: reviewed };
      })
      .filter((j) => !status || j.status === status);
    return json({ jobs: list });
  }

  const m = path.match(/^\/api\/v1\/jobs\/([^/]+)(\/report|\/media|\/reviews|\/export|\/evidence\/([^/]+)|\/events\/([^/]+)\/review)?$/);
  if (!m) return err(404, "not_found", "Not found");
  const jobId = decodeURIComponent(m[1]);
  const job = jobs.get(jobId);
  if (!job) return err(404, "not_found", "Not found");
  const terminalNow = job.polls >= STAGES.length;

  if (m[4] !== undefined) {
    if (method !== "POST") return err(405, "method_not_allowed", "Method not allowed");
    if (!terminalNow || job.scenario === "fail") return err(409, "not_done", "Job not done");
    const eventId = decodeURIComponent(m[4]);
    const report = terminalReport(job, jobId) as { events?: { event_id: string }[] };
    if (eventId !== "missed" && !(report.events ?? []).some((e) => e.event_id === eventId)) {
      return err(404, "not_found", "Unknown event");
    }
    const body = JSON.parse(String(init?.body ?? "{}")) as Record<string, unknown>;
    const review = {
      review_id: `fx-${++reviewSeq}`,
      event_id: eventId,
      decision: body.decision,
      comment: body.comment ?? "",
      corrected_type: body.corrected_type ?? null,
      corrected_timecode_sec: body.corrected_timecode_sec ?? null,
      reviewer: body.reviewer ?? "",
      reviewed_at: new Date().toISOString(),
    };
    reviewsOf(jobId, (report as { reviews?: unknown[] }).reviews).push(review);
    return json(review, 201);
  }
  if (m[2] === "/reviews") {
    if (!terminalNow || job.scenario === "fail") return json([]);
    return json(reviewsOf(jobId, (terminalReport(job, jobId) as { reviews?: unknown[] }).reviews));
  }
  if (m[2] === "/export") {
    if (!terminalNow) return err(409, "not_done", "Job not done");
    const report = terminalReport(job, jobId);
    const bundle = {
      job_id: jobId,
      exported_at: new Date().toISOString(),
      report,
      versions: null,
      coverage: null,
      original_jury_result: job.scenario === "fail" ? null : job.scenario === "clean" ? juryClean : juryViolation,
    };
    return new Response(JSON.stringify(bundle, null, 2), {
      status: 200,
      headers: {
        "Content-Type": "application/json",
        "Content-Disposition": `attachment; filename="cashwatch-${jobId}-fixture.json"`,
      },
    });
  }

  if (m[2]?.startsWith("/evidence/")) {
    const name = m[3];
    const asset = name.startsWith("e2") ? e2Url : e1Url;
    const blob = await (await realFetch(asset)).blob();
    return new Response(blob, { status: 200, headers: { "Content-Type": "image/jpeg" } });
  }
  if (m[2] === "/media") {
    // No synthetic video is shipped: return bytes the browser cannot decode to
    // exercise the evidence-only fallback.
    return new Response(new Blob([new Uint8Array(64)], { type: "video/quicktime" }), { status: 200 });
  }

  const terminal = job.polls >= STAGES.length;
  if (m[2] === "/report") {
    if (!terminal) return json(processingAt(job, jobId));
    return json(terminalReport(job, jobId));
  }

  // GET /api/v1/jobs/{id}
  job.polls += 1;
  if (job.scenario === "flaky" && job.polls % 3 === 0 && job.polls < STAGES.length + 3) {
    return err(503, "not_ready", "Service unavailable");
  }
  if (job.polls <= STAGES.length) return json({ job_id: jobId, status: "processing" });
  if (job.scenario === "fail") return json({ job_id: jobId, status: "failed", error: failedReport.error });
  if (job.scenario === "clean") return json({ ...juryClean, job_id: jobId });
  return json({ ...juryViolation, job_id: jobId });
}

class MockXHR {
  status = 0;
  responseText = "";
  readyState = 0;
  upload: { onprogress: ((ev: ProgressEvent) => void) | null } = { onprogress: null };
  onload: (() => void) | null = null;
  onerror: (() => void) | null = null;
  onabort: (() => void) | null = null;
  private headers: Record<string, string> = {};
  private respHeaders: Record<string, string> = {};
  private timer: number | undefined;
  private aborted = false;

  open(_method: string, _url: string): void {
    this.readyState = 1;
  }
  setRequestHeader(name: string, value: string): void {
    this.headers[name.toLowerCase()] = value;
  }
  getResponseHeader(name: string): string | null {
    return this.respHeaders[name.toLowerCase()] ?? null;
  }
  abort(): void {
    this.aborted = true;
    window.clearTimeout(this.timer);
    this.onabort?.();
  }
  send(body: FormData): void {
    const file = body.get("file") as File | null;
    const total = file?.size ?? 0;
    const name = (file?.name ?? "").toLowerCase();
    let loaded = 0;
    const step = () => {
      if (this.aborted) return;
      loaded = Math.min(total, loaded + Math.max(1, Math.ceil(total / 10)));
      this.upload.onprogress?.({ lengthComputable: true, loaded, total } as ProgressEvent);
      if (loaded < total) {
        this.timer = window.setTimeout(step, 150);
        return;
      }
      this.finish(name);
    };
    this.timer = window.setTimeout(step, 150);
  }
  private finish(name: string): void {
    this.readyState = 4;
    if (!this.headers["x-api-key"] || this.headers["x-api-key"] === "bad") {
      this.status = 401;
      this.responseText = JSON.stringify({ error: { code: "unauthorized", message: "Unauthorized" } });
    } else if (name.includes("busy")) {
      this.status = 429;
      this.respHeaders["retry-after"] = "30";
      this.responseText = JSON.stringify({ error: { code: "capacity", message: "Busy" } });
    } else {
      const scenario: Scenario = name.includes("clean")
        ? "clean"
        : name.includes("fail")
          ? "fail"
          : name.includes("flaky")
            ? "flaky"
            : "violation";
      jobs.set(JOB_ID, { scenario, polls: 0 });
      reviewsByJob.delete(JOB_ID);
      startTimes.set(JOB_ID, Date.now());
      this.status = 202;
      this.responseText = JSON.stringify({ job_id: JOB_ID });
    }
    this.onload?.();
  }
}

// Allow refresh-recovery demos: any stored id resumes as a violation scenario.
try {
  const stored = window.sessionStorage.getItem("cashwatch.job_id");
  if (stored && !jobs.has(stored)) {
    jobs.set(stored, { scenario: "violation", polls: 0 });
    startTimes.set(stored, Date.now());
  }
} catch {
  /* ignore */
}

window.fetch = mockFetch as typeof window.fetch;
(window as unknown as { XMLHttpRequest: unknown }).XMLHttpRequest = MockXHR;

const banner = document.createElement("div");
banner.setAttribute("role", "note");
banner.textContent = "ФИКСТУРЫ — синтетические данные, сервер не используется, анализ не выполняется";
banner.style.cssText =
  "position:sticky;top:0;z-index:1000;background:#5b21b6;color:#fff;font-weight:700;padding:8px 16px;text-align:center;letter-spacing:.02em";
document.body.prepend(banner);

console.warn("[cashwatch] FIXTURE MODE: mock API layer installed");
