import { useEffect, useState } from "react";
import type { PollStatus } from "../App";
import { formatElapsed } from "../format";
import { STAGE_STEPS, progressText, stepIndex } from "../stages";

interface Props {
  jobId: string;
  status: PollStatus;
  startedAt: number;
  onReconnect: () => void;
  onCancel: () => void;
}

export function ProcessingView({ jobId, status, startedAt, onReconnect, onCancel }: Props) {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const t = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(t);
  }, []);

  const report = status.report;
  // Upload ("Загрузка") is finished once a job id exists; before the first
  // report arrives we only know the server accepted the file.
  const current = report ? stepIndex(report.stage) : 1;
  const elapsed = report ? report.elapsed_sec + Math.max(0, now - status.reportAt) / 1000 : (now - startedAt) / 1000;
  const progress = report?.progress && report.progress.total > 0 ? report.progress : null;

  return (
    <section className="card" aria-labelledby="processing-title" aria-busy={!status.lost}>
      <h2 id="processing-title">Идёт анализ</h2>
      <p className="muted">
        Задание <code>{jobId}</code> · прошло {formatElapsed(elapsed)}
        <span className="hint"> (длительность анализа заранее не известна точно)</span>
      </p>

      <ol className="stages">
        {STAGE_STEPS.map((s, i) => {
          const state = i < current ? "done" : i === current ? "active" : "pending";
          const text = state === "done" ? "выполнено" : state === "active" ? "выполняется" : "ожидает";
          return (
            <li key={s.label} className={`stage stage-${state}`} aria-current={state === "active" ? "step" : undefined}>
              <span className="stage-mark" aria-hidden="true">
                {state === "done" ? "✓" : state === "active" ? "●" : "○"}
              </span>
              <span className="stage-label">{s.label}</span>
              <span className="stage-state">— {text}</span>
            </li>
          );
        })}
      </ol>

      {progress && (
        <div className="field">
          <label className="progress-label" htmlFor="stage-progress">
            Текущий этап: {progressText(progress)}
          </label>
          <progress id="stage-progress" max={progress.total} value={Math.min(progress.completed, progress.total)} />
        </div>
      )}

      {status.connIssue && (
        <div className="notice notice-warn" role="alert">
          <p>
            <strong>{status.lost ? "Связь с сервером потеряна. " : ""}</strong>
            {status.connIssue}
          </p>
          <p className="hint">Это проблема соединения, а не результат анализа.</p>
          {status.lost && (
            <button type="button" className="btn btn-primary" onClick={onReconnect}>
              Повторить подключение
            </button>
          )}
        </div>
      )}

      <button type="button" className="btn" onClick={onCancel}>
        Прекратить отслеживание
      </button>
    </section>
  );
}
