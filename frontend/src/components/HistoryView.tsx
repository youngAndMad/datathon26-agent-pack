import { useEffect, useId, useState, type RefObject } from "react";
import { ApiError, listJobs } from "../api";
import { formatDateTime } from "../format";
import { ERROR_MESSAGES_RU, REVIEW_REQUIRED_BADGE } from "../messages";
import type { JobSummary } from "../types";

interface Props {
  headingRef: RefObject<HTMLHeadingElement | null>;
  apiKey: string;
  onOpen: (job: JobSummary) => void;
  onBack: () => void;
  onUnauthorized: () => void;
}

const STATUS_RU: Record<string, string> = {
  processing: "В обработке",
  done: "Завершено",
  failed: "Ошибка",
};

function verdictText(j: JobSummary): string {
  if (j.status === "failed") return j.error_code ? (ERROR_MESSAGES_RU[j.error_code] ?? j.error_code) : "Анализ не выполнен";
  if (j.status !== "done") return "—";
  if (j.verdict === "violation") return "Возможное нарушение";
  if (j.verdict === "no_violation") return "Нарушение не обнаружено";
  return "—";
}

export function HistoryView({ headingRef, apiKey, onOpen, onBack, onUnauthorized }: Props) {
  const id = useId();
  const [status, setStatus] = useState("");
  const [gen, setGen] = useState(0);
  const [state, setState] = useState<
    { kind: "loading" } | { kind: "ok"; jobs: JobSummary[] } | { kind: "error"; message: string }
  >({ kind: "loading" });

  useEffect(() => {
    const ctrl = new AbortController();
    setState({ kind: "loading" });
    listJobs(apiKey, { limit: 50, status: status || undefined }, ctrl.signal).then(
      (r) => {
        if (!ctrl.signal.aborted) setState({ kind: "ok", jobs: r.jobs });
      },
      (e: unknown) => {
        if (ctrl.signal.aborted) return;
        if (e instanceof ApiError && e.unauthorized) {
          onUnauthorized();
          return;
        }
        setState({ kind: "error", message: e instanceof ApiError ? e.message : "Не удалось загрузить историю." });
      },
    );
    return () => ctrl.abort();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [apiKey, status, gen]);

  return (
    <section className="card" aria-labelledby={`${id}-title`} data-testid="history">
      <h2 id={`${id}-title`} ref={headingRef} tabIndex={-1}>
        История проверок
      </h2>
      <div className="row history-controls">
        <div className="field">
          <label htmlFor={`${id}-status`}>Статус</label>
          <select id={`${id}-status`} value={status} onChange={(e) => setStatus(e.target.value)}>
            <option value="">Все</option>
            <option value="processing">В обработке</option>
            <option value="done">Завершено</option>
            <option value="failed">Ошибка</option>
          </select>
        </div>
        <button type="button" className="btn" onClick={() => setGen((g) => g + 1)}>
          Обновить
        </button>
        <button type="button" className="btn" onClick={onBack}>
          Назад к загрузке
        </button>
      </div>

      {state.kind === "loading" && <p className="hint">Загрузка списка…</p>}
      {state.kind === "error" && (
        <p className="notice notice-error" role="alert">
          {state.message}
        </p>
      )}
      {state.kind === "ok" && state.jobs.length === 0 && <p className="hint">Проверок пока нет.</p>}
      {state.kind === "ok" && state.jobs.length > 0 && (
        <div className="table-wrap">
          <table className="history-table">
            <caption className="sr-only">Последние проверки, сначала новые</caption>
            <thead>
              <tr>
                <th scope="col">Время</th>
                <th scope="col">Статус</th>
                <th scope="col">Результат</th>
                <th scope="col">События</th>
                <th scope="col">Проверено</th>
                <th scope="col">Просмотр</th>
                <th scope="col">Режим</th>
                <th scope="col">
                  <span className="sr-only">Действие</span>
                </th>
              </tr>
            </thead>
            <tbody>
              {state.jobs.map((j) => (
                <tr key={j.job_id} data-testid={`history-row-${j.job_id}`}>
                  <td>
                    {formatDateTime(j.received_at)}
                    <div className="hint">
                      <code>{j.job_id}</code>
                    </div>
                  </td>
                  <td>{STATUS_RU[j.status] ?? j.status}</td>
                  <td>{verdictText(j)}</td>
                  <td>{j.status === "done" ? (j.event_count ?? 0) : "—"}</td>
                  <td>{j.status === "done" ? `${j.reviewed_event_count ?? 0} из ${j.event_count ?? 0}` : "—"}</td>
                  <td>{j.review_required ? <span className="badge badge-review">{REVIEW_REQUIRED_BADGE}</span> : "—"}</td>
                  <td>{j.mode === "test" ? <span className="badge badge-test">ТЕСТ</span> : "рабочий"}</td>
                  <td>
                    <button
                      type="button"
                      className="btn btn-small"
                      onClick={() => onOpen(j)}
                      aria-label={`Открыть проверку ${j.job_id}`}
                    >
                      {j.status === "processing" ? "Следить" : "Открыть"}
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}
