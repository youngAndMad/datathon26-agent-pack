import { useCallback, useEffect, useRef, useState } from "react";
import { ApiError, getJob, getReport, uploadVideo, type UploadHandle } from "./api";
import { ERROR_MESSAGES_RU, MAX_UPLOAD_BYTES, TEST_MODE_BANNER, VERDICT_CLEAN, VERDICT_VIOLATION } from "./messages";
import type { JobDone, UiReport } from "./types";
import { UploadForm } from "./components/UploadForm";
import { UploadProgress } from "./components/UploadProgress";
import { ProcessingView } from "./components/ProcessingView";
import { ResultView } from "./components/ResultView";
import { FailedView } from "./components/FailedView";
import { STAGE_STEPS, stepIndex } from "./stages";

export const JOB_STORAGE_KEY = "cashwatch.job_id";

export interface AppConfig {
  pollIntervalMs?: number;
  maxBackoffMs?: number;
  maxConsecutiveFailures?: number;
}

type Phase =
  | { kind: "idle" }
  | { kind: "uploading"; loaded: number; total: number }
  | { kind: "processing"; jobId: string }
  | { kind: "done"; jobId: string; job: JobDone; report: UiReport }
  | { kind: "failed"; jobId: string | null; message: string; report: UiReport | null };

export interface PollStatus {
  report: UiReport | null;
  reportAt: number;
  connIssue: string | null;
  lost: boolean;
}

// Persisting the job id (never the key) lets polling resume after a refresh.
function readStoredJob(): string | null {
  try {
    return window.sessionStorage.getItem(JOB_STORAGE_KEY);
  } catch {
    return null;
  }
}
function storeJob(jobId: string | null): void {
  try {
    if (jobId) window.sessionStorage.setItem(JOB_STORAGE_KEY, jobId);
    else window.sessionStorage.removeItem(JOB_STORAGE_KEY);
  } catch {
    /* storage unavailable: recovery after refresh simply won't work */
  }
}

function sleep(ms: number, signal: AbortSignal): Promise<void> {
  return new Promise((resolve) => {
    const t = window.setTimeout(resolve, ms);
    signal.addEventListener("abort", () => {
      window.clearTimeout(t);
      resolve();
    });
  });
}

function failureMessage(code: string | undefined, message: string | undefined): string {
  if (message && message.trim()) return message.trim();
  if (code && ERROR_MESSAGES_RU[code]) return ERROR_MESSAGES_RU[code];
  return "Анализ не выполнен.";
}

/** Jury object and UI report must agree before a result is displayed. */
function consistent(job: JobDone, report: UiReport): boolean {
  const n = job.violations?.length ?? 0;
  if (job.verdict !== "violation" && job.verdict !== "no_violation") return false;
  if ((job.verdict === "violation") !== n > 0) return false;
  return report.status === "done" && report.verdict === job.verdict && (report.events?.length ?? 0) === n;
}

export default function App({ config = {} }: { config?: AppConfig }) {
  const pollIntervalMs = config.pollIntervalMs ?? 1500;
  const maxBackoffMs = config.maxBackoffMs ?? 15000;
  const maxFailures = config.maxConsecutiveFailures ?? 40;

  const [apiKey, setApiKey] = useState("");
  const [file, setFile] = useState<File | null>(null);
  const [formError, setFormError] = useState<string | null>(null);
  const [pendingJobId, setPendingJobId] = useState<string | null>(() => readStoredJob());
  const [phase, setPhase] = useState<Phase>({ kind: "idle" });
  const [poll, setPoll] = useState<PollStatus>({ report: null, reportAt: 0, connIssue: null, lost: false });
  const [pollGen, setPollGen] = useState(0);
  const [startedAt, setStartedAt] = useState(() => Date.now());
  const uploadRef = useRef<UploadHandle | null>(null);
  const headingRef = useRef<HTMLHeadingElement | null>(null);

  const beginProcessing = useCallback((jobId: string) => {
    storeJob(jobId);
    setPendingJobId(null);
    setPoll({ report: null, reportAt: 0, connIssue: null, lost: false });
    setPhase({ kind: "processing", jobId });
  }, []);

  const startUpload = useCallback(
    (f: File | null) => {
      if (phase.kind === "uploading" || phase.kind === "processing") return; // no duplicate submissions
      setFormError(null);
      if (!apiKey.trim()) {
        setFormError("Введите ключ доступа.");
        return;
      }
      if (!f) {
        setFormError("Выберите видеофайл MOV.");
        return;
      }
      if (f.size > MAX_UPLOAD_BYTES) {
        setFormError("Файл слишком большой. Максимальный размер — 256 МБ.");
        setPhase({ kind: "idle" });
        return;
      }
      if (f.size === 0) {
        setFormError("Файл пустой.");
        setPhase({ kind: "idle" });
        return;
      }
      setStartedAt(Date.now());
      setPhase({ kind: "uploading", loaded: 0, total: f.size });
      const handle = uploadVideo(f, apiKey.trim(), (loaded, total) =>
        setPhase((p) => (p.kind === "uploading" ? { kind: "uploading", loaded, total } : p)),
      );
      uploadRef.current = handle;
      handle.promise.then(
        ({ job_id }) => {
          uploadRef.current = null;
          beginProcessing(job_id);
        },
        (e: unknown) => {
          uploadRef.current = null;
          setFormError(e instanceof ApiError ? e.message : "Не удалось загрузить файл.");
          setPhase({ kind: "idle" });
        },
      );
    },
    [apiKey, phase.kind, beginProcessing],
  );

  const resumePending = useCallback(() => {
    if (!pendingJobId) return;
    if (!apiKey.trim()) {
      setFormError("Введите ключ доступа, чтобы продолжить.");
      return;
    }
    setFormError(null);
    setStartedAt(Date.now());
    beginProcessing(pendingJobId);
  }, [apiKey, pendingJobId, beginProcessing]);

  const reset = useCallback(() => {
    uploadRef.current?.abort();
    uploadRef.current = null;
    storeJob(null);
    setPendingJobId(null);
    setFormError(null);
    setPoll({ report: null, reportAt: 0, connIssue: null, lost: false });
    setPhase({ kind: "idle" });
  }, []);

  // ---------------------------------------------------------------- polling
  const processingJobId = phase.kind === "processing" ? phase.jobId : null;
  useEffect(() => {
    if (!processingJobId) return;
    const jobId = processingJobId;
    const key = apiKey.trim();
    const ctrl = new AbortController();
    const signal = ctrl.signal;

    const run = async () => {
      let failures = 0;
      while (!signal.aborted) {
        let delay = pollIntervalMs;
        try {
          const job = await getJob(jobId, key, signal);
          if (job.status === "processing") {
            try {
              const report = await getReport(jobId, key, signal);
              if (!signal.aborted) setPoll({ report, reportAt: Date.now(), connIssue: null, lost: false });
            } catch (e) {
              if (e instanceof ApiError && e.unauthorized) throw e;
              // Report is supplementary while processing; status poll succeeded.
              if (!signal.aborted) setPoll((p) => ({ ...p, connIssue: null, lost: false }));
            }
            failures = 0;
          } else if (job.status === "failed") {
            let report: UiReport | null = null;
            try {
              report = await getReport(jobId, key, signal);
            } catch {
              report = null;
            }
            if (signal.aborted) return;
            setPhase({
              kind: "failed",
              jobId,
              message: failureMessage(job.error?.code, job.error?.message),
              report,
            });
            return;
          } else {
            const report = await getReport(jobId, key, signal);
            if (signal.aborted) return;
            if (report.status === "processing") {
              throw new ApiError("protocol", "Результат ещё публикуется сервером.");
            }
            if (!consistent(job, report)) {
              setPhase({
                kind: "failed",
                jobId,
                message: "Ответ сервера несогласован, результат не может быть показан.",
                report: null,
              });
              return;
            }
            setPhase({ kind: "done", jobId, job, report });
            return;
          }
        } catch (e) {
          if (signal.aborted) return;
          const err = e instanceof ApiError ? e : new ApiError("network", "Нет соединения с сервером.");
          if (err.unauthorized) {
            setPendingJobId(jobId);
            setFormError("Неверный ключ доступа. Введите ключ ещё раз, чтобы продолжить.");
            setPhase({ kind: "idle" });
            return;
          }
          if (err.kind === "http" && (err.status === 404 || err.status === 410)) {
            storeJob(null);
            setPhase({ kind: "failed", jobId, message: err.message, report: null });
            return;
          }
          failures += 1;
          if (failures >= maxFailures) {
            setPoll((p) => ({ ...p, connIssue: err.message, lost: true }));
            return;
          }
          delay = Math.min(pollIntervalMs * 2 ** failures, maxBackoffMs);
          if (err.retryAfterSec !== undefined) {
            delay = Math.min(Math.max(delay, err.retryAfterSec * 1000), maxBackoffMs);
          }
          setPoll((p) => ({
            ...p,
            connIssue: `Проблема соединения: ${err.message} Повторная попытка через ${Math.ceil(delay / 1000)} с.`,
            lost: false,
          }));
        }
        await sleep(delay, signal);
      }
    };
    void run();
    return () => ctrl.abort();
    // apiKey is intentionally read once per polling session (input is locked while processing).
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [processingJobId, pollGen, pollIntervalMs, maxBackoffMs, maxFailures]);

  useEffect(() => {
    if (phase.kind === "done" || phase.kind === "failed") headingRef.current?.focus();
  }, [phase.kind]);

  useEffect(() => () => uploadRef.current?.abort(), []);

  // ---------------------------------------------------------------- render
  const activeReport =
    phase.kind === "done" ? phase.report : phase.kind === "failed" ? phase.report : phase.kind === "processing" ? poll.report : null;
  const testMode = activeReport?.mode === "test";

  let liveStatus = "";
  if (phase.kind === "uploading") {
    liveStatus = `Загрузка файла: ${phase.total ? Math.floor((phase.loaded / phase.total) * 100) : 0}%`;
  } else if (phase.kind === "processing") {
    const idx = poll.report ? stepIndex(poll.report.stage) : 1;
    liveStatus = poll.connIssue ?? `Обработка: ${STAGE_STEPS[Math.min(idx, STAGE_STEPS.length - 1)].label}`;
  } else if (phase.kind === "done") {
    liveStatus = `Анализ завершён: ${phase.job.verdict === "violation" ? VERDICT_VIOLATION : VERDICT_CLEAN}`;
  } else if (phase.kind === "failed") {
    liveStatus = `Анализ не выполнен: ${phase.message}`;
  } else if (formError) {
    liveStatus = formError;
  }

  const busy = phase.kind === "uploading" || phase.kind === "processing";

  return (
    <div className="app">
      {testMode && (
        <div className="banner banner-test" role="alert">
          {TEST_MODE_BANNER}
        </div>
      )}
      <header className="app-header">
        <h1>Проверка видеозаписи кассовых операций</h1>
        <p className="muted">Локальный анализ видимых действий на соответствие проверяемым правилам.</p>
      </header>

      <p className="sr-only" role="status" aria-live="polite" data-testid="live-status">
        {liveStatus}
      </p>

      <main>
        {(phase.kind === "idle" || phase.kind === "uploading") && (
          <UploadForm
            apiKey={apiKey}
            onApiKeyChange={setApiKey}
            file={file}
            onFileChange={(f) => {
              setFile(f);
              setFormError(null);
            }}
            disabled={busy}
            error={formError}
            pendingJobId={pendingJobId}
            onSubmit={() => startUpload(file)}
            onResume={resumePending}
            onDiscardPending={reset}
          />
        )}
        {phase.kind === "uploading" && (
          <UploadProgress loaded={phase.loaded} total={phase.total} onCancel={() => uploadRef.current?.abort()} />
        )}
        {phase.kind === "processing" && (
          <ProcessingView
            jobId={phase.jobId}
            status={poll}
            startedAt={startedAt}
            onReconnect={() => {
              setPoll((p) => ({ ...p, connIssue: null, lost: false }));
              setPollGen((g) => g + 1);
            }}
            onCancel={reset}
          />
        )}
        {phase.kind === "done" && (
          <ResultView
            headingRef={headingRef}
            apiKey={apiKey.trim()}
            jobId={phase.jobId}
            job={phase.job}
            report={phase.report}
            onReset={reset}
          />
        )}
        {phase.kind === "failed" && (
          <FailedView
            headingRef={headingRef}
            message={phase.message}
            canRetrySameFile={file !== null}
            onRetry={() => {
              storeJob(null);
              startUpload(file);
            }}
            onChooseAnother={reset}
          />
        )}
      </main>
    </div>
  );
}
