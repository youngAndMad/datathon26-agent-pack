// Same-origin API client. The access key is passed in by the caller on every
// request and is never stored by this module.

import { ERROR_MESSAGES_RU } from "./messages";
import type { EventReview, JobListResponse, JobResponse, ReviewRequest, UiReport } from "./types";

export type ApiErrorKind = "http" | "network" | "aborted" | "protocol";

export class ApiError extends Error {
  readonly kind: ApiErrorKind;
  readonly status?: number;
  readonly code?: string;
  readonly retryAfterSec?: number;

  constructor(
    kind: ApiErrorKind,
    message: string,
    opts: { status?: number; code?: string; retryAfterSec?: number } = {},
  ) {
    super(message);
    this.name = "ApiError";
    this.kind = kind;
    this.status = opts.status;
    this.code = opts.code;
    this.retryAfterSec = opts.retryAfterSec;
  }

  /** Temporary condition: polling should retry, never treat it as a result. */
  get transient(): boolean {
    if (this.kind === "network" || this.kind === "protocol") return true;
    if (this.kind !== "http" || this.status === undefined) return false;
    return this.status === 429 || this.status === 408 || this.status >= 500;
  }

  get unauthorized(): boolean {
    return this.kind === "http" && this.status === 401;
  }
}

export function parseRetryAfter(value: string | null, now = Date.now()): number | undefined {
  if (!value) return undefined;
  const trimmed = value.trim();
  if (/^\d+$/.test(trimmed)) return Number(trimmed);
  const date = Date.parse(trimmed);
  if (Number.isNaN(date)) return undefined;
  return Math.max(0, Math.ceil((date - now) / 1000));
}

function extractCode(body: unknown): string | undefined {
  if (body && typeof body === "object" && "error" in body) {
    const err = (body as { error: unknown }).error;
    if (err && typeof err === "object" && "code" in err) {
      const code = (err as { code: unknown }).code;
      if (typeof code === "string") return code;
    }
  }
  return undefined;
}

/** Russian user-facing message for an HTTP error status. */
export function messageForStatus(status: number, code?: string, retryAfterSec?: number): string {
  const detail = code && ERROR_MESSAGES_RU[code] ? ` ${ERROR_MESSAGES_RU[code]}.` : "";
  switch (status) {
    case 401:
      return "Неверный ключ доступа. Проверьте ключ и повторите.";
    case 400:
    case 422:
      return `Файл или запрос не приняты сервером.${detail || " Проверьте, что это корректное видео MOV."}`;
    case 404:
      return "Задание не найдено.";
    case 409:
      return "Повторная отправка с другим файлом отклонена сервером.";
    case 410:
      return "Срок хранения результата истёк.";
    case 413:
      return "Файл слишком большой. Максимальный размер — 256 МБ.";
    case 415:
      return "Формат файла не поддерживается. Загрузите видео в формате MOV.";
    case 429:
      return retryAfterSec !== undefined
        ? `Сервис занят другими заданиями. Повторите через ${retryAfterSec} с.`
        : "Сервис занят другими заданиями. Повторите позже.";
    case 503:
      return "Сервис не готов к анализу. Повторите позже.";
    default:
      if (status >= 500) return `Ошибка сервера (${status}). Повторите позже.`;
      return `Неожиданный ответ сервера (${status}).`;
  }
}

function httpError(status: number, bodyText: string, retryAfterHeader: string | null): ApiError {
  let body: unknown;
  try {
    body = bodyText ? JSON.parse(bodyText) : undefined;
  } catch {
    body = undefined;
  }
  const code = extractCode(body);
  const retryAfterSec = status === 429 || status === 503 ? parseRetryAfter(retryAfterHeader) : undefined;
  return new ApiError("http", messageForStatus(status, code, retryAfterSec), { status, code, retryAfterSec });
}

/** Only same-origin API paths may receive the access key. */
export function isApiPath(url: string): boolean {
  return /^\/api\/v1\/[A-Za-z0-9._~\-/]+(\?[A-Za-z0-9_.\-=&]*)?$/.test(url) && !url.includes("..");
}

interface FetchOpts {
  method?: "GET" | "POST";
  json?: unknown;
}

async function authorizedFetch(path: string, key: string, signal?: AbortSignal, opts: FetchOpts = {}): Promise<Response> {
  if (!isApiPath(path)) {
    throw new ApiError("protocol", "Недопустимый адрес ресурса.");
  }
  const headers: Record<string, string> = { "X-API-Key": key };
  if (opts.json !== undefined) headers["Content-Type"] = "application/json";
  let res: Response;
  try {
    res = await fetch(path, {
      method: opts.method ?? "GET",
      headers,
      body: opts.json !== undefined ? JSON.stringify(opts.json) : undefined,
      credentials: "same-origin",
      cache: "no-store",
      signal,
    });
  } catch (e) {
    if (signal?.aborted) throw new ApiError("aborted", "Запрос отменён.");
    throw new ApiError("network", "Нет соединения с сервером.");
  }
  if (!res.ok) {
    const text = await res.text().catch(() => "");
    throw httpError(res.status, text, res.headers.get("Retry-After"));
  }
  return res;
}

async function getJson<T>(path: string, key: string, signal?: AbortSignal, opts?: FetchOpts): Promise<T> {
  const res = await authorizedFetch(path, key, signal, opts);
  try {
    return (await res.json()) as T;
  } catch {
    throw new ApiError("protocol", "Сервер вернул некорректный ответ.");
  }
}

const jobPath = (jobId: string) => `/api/v1/jobs/${encodeURIComponent(jobId)}`;

export async function getJob(jobId: string, key: string, signal?: AbortSignal): Promise<JobResponse> {
  const job = await getJson<JobResponse>(jobPath(jobId), key, signal);
  if (!job || typeof job !== "object" || !["processing", "done", "failed"].includes(job.status)) {
    throw new ApiError("protocol", "Сервер вернул некорректный статус задания.");
  }
  return job;
}

export function getReport(jobId: string, key: string, signal?: AbortSignal): Promise<UiReport> {
  return getJson<UiReport>(`${jobPath(jobId)}/report`, key, signal);
}

export function mediaPath(jobId: string): string {
  return `${jobPath(jobId)}/media`;
}

/** Fetch a protected binary (evidence JPEG, preview video) with the key header. */
export async function fetchBlob(path: string, key: string, signal?: AbortSignal): Promise<Blob> {
  const res = await authorizedFetch(path, key, signal);
  return res.blob();
}

// ------------------------------------------------------------ reviews / history / export

/** Event id used by the backend for "missed event" reports. */
export const MISSED_EVENT_ID = "missed";

/**
 * POST a human reviewer decision. It is stored separately by the server and
 * never changes the system (jury) result.
 */
export async function postReview(
  jobId: string,
  eventId: string,
  body: ReviewRequest,
  key: string,
  signal?: AbortSignal,
): Promise<EventReview> {
  try {
    const r = await getJson<EventReview>(
      `${jobPath(jobId)}/events/${encodeURIComponent(eventId)}/review`,
      key,
      signal,
      { method: "POST", json: body },
    );
    if (!r || typeof r !== "object" || typeof r.review_id !== "string" || typeof r.decision !== "string") {
      throw new ApiError("protocol", "Сервер вернул некорректный ответ на сохранение решения.");
    }
    return r;
  } catch (e) {
    if (e instanceof ApiError && e.kind === "http") {
      if (e.status === 409) throw new ApiError("http", "Задание ещё не завершено — решение нельзя сохранить.", { status: 409 });
      if (e.status === 404) throw new ApiError("http", "Событие не найдено на сервере.", { status: 404 });
      if (e.status === 422 || e.status === 400) {
        throw new ApiError("http", "Сервер не принял решение: проверьте поля формы.", { status: e.status });
      }
    }
    throw e;
  }
}

export function getReviews(jobId: string, key: string, signal?: AbortSignal): Promise<EventReview[]> {
  return getJson<EventReview[]>(`${jobPath(jobId)}/reviews`, key, signal);
}

export async function listJobs(
  key: string,
  opts: { limit?: number; status?: string } = {},
  signal?: AbortSignal,
): Promise<JobListResponse> {
  const q = new URLSearchParams({ limit: String(opts.limit ?? 50) });
  if (opts.status) q.set("status", opts.status);
  const body = await getJson<JobListResponse>(`/api/v1/jobs?${q.toString()}`, key, signal);
  if (!body || !Array.isArray(body.jobs)) throw new ApiError("protocol", "Сервер вернул некорректный список заданий.");
  return body;
}

/** Filename from Content-Disposition (RFC 6266 filename* preferred), sanitized. */
export function filenameFromDisposition(header: string | null, fallback: string): string {
  if (!header) return fallback;
  let name: string | null = null;
  const star = /filename\*\s*=\s*(?:UTF-8|utf-8)''([^;]+)/.exec(header);
  if (star) {
    try {
      name = decodeURIComponent(star[1].trim().replace(/^"|"$/g, ""));
    } catch {
      name = null;
    }
  }
  if (!name) {
    const plain = /filename\s*=\s*("([^"]*)"|[^;]+)/.exec(header);
    if (plain) name = (plain[2] ?? plain[1]).trim();
  }
  if (!name) return fallback;
  const safe = name.replace(/[\\/:*?"<>|\x00-\x1f]/g, "_").replace(/^\.+/, "").slice(0, 120);
  return safe || fallback;
}

export async function fetchExport(jobId: string, key: string, signal?: AbortSignal): Promise<{ blob: Blob; filename: string }> {
  const res = await authorizedFetch(`${jobPath(jobId)}/export`, key, signal);
  const blob = await res.blob();
  const fallback = `cashwatch-${jobId.replace(/[^A-Za-z0-9._-]/g, "_")}.json`;
  return { blob, filename: filenameFromDisposition(res.headers.get("Content-Disposition"), fallback) };
}

export interface UploadHandle {
  promise: Promise<{ job_id: string }>;
  abort: () => void;
}

/**
 * POST /api/v1/analyze via XMLHttpRequest so that real upload byte progress is
 * available (fetch has no upload progress events).
 */
export function uploadVideo(
  file: File,
  key: string,
  onProgress: (loaded: number, total: number) => void,
): UploadHandle {
  const xhr = new XMLHttpRequest();
  const promise = new Promise<{ job_id: string }>((resolve, reject) => {
    xhr.open("POST", "/api/v1/analyze", true);
    xhr.setRequestHeader("X-API-Key", key);
    xhr.upload.onprogress = (ev: ProgressEvent) => {
      if (ev.lengthComputable) onProgress(ev.loaded, ev.total);
    };
    xhr.onload = () => {
      if (xhr.status === 202 || xhr.status === 200) {
        try {
          const body = JSON.parse(xhr.responseText) as { job_id?: unknown };
          if (typeof body.job_id === "string" && body.job_id.length > 0) {
            resolve({ job_id: body.job_id });
            return;
          }
        } catch {
          /* fallthrough */
        }
        reject(new ApiError("protocol", "Сервер вернул некорректный ответ на загрузку."));
        return;
      }
      reject(httpError(xhr.status, xhr.responseText ?? "", xhr.getResponseHeader("Retry-After")));
    };
    xhr.onerror = () => reject(new ApiError("network", "Нет соединения с сервером. Загрузка прервана."));
    xhr.onabort = () => reject(new ApiError("aborted", "Загрузка отменена."));
    const form = new FormData();
    form.append("file", file, file.name);
    xhr.send(form);
  });
  return { promise, abort: () => xhr.abort() };
}
