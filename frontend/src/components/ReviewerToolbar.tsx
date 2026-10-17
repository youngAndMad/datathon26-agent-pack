import { useId, useState } from "react";
import { ApiError, MISSED_EVENT_ID, fetchExport } from "../api";
import { formatDateTime, formatTimecode, parseTimeInput } from "../format";
import type { EventReview, ReviewRequest } from "../types";

interface Props {
  jobId: string;
  apiKey: string;
  reviewer: string;
  onReviewerChange: (name: string) => void;
  missed: EventReview[];
  submit: (eventId: string, req: ReviewRequest) => Promise<void>;
  currentTime: () => number | null;
}

/** Trigger a browser download of an in-memory blob, then revoke its object URL. */
export function downloadBlob(blob: Blob, filename: string): void {
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  a.rel = "noopener";
  a.style.display = "none";
  document.body.appendChild(a);
  try {
    a.click();
  } finally {
    a.remove();
    window.setTimeout(() => URL.revokeObjectURL(url), 1000);
  }
}

export function ReviewerToolbar({ jobId, apiKey, reviewer, onReviewerChange, missed, submit, currentTime }: Props) {
  const id = useId();
  const [exportState, setExportState] = useState<{ busy: boolean; error: string | null; done: string | null }>({
    busy: false,
    error: null,
    done: null,
  });
  const [form, setForm] = useState<{ time: string; type: string; comment: string } | null>(null);
  const [formBusy, setFormBusy] = useState(false);
  const [formError, setFormError] = useState<string | null>(null);

  const doExport = async () => {
    setExportState({ busy: true, error: null, done: null });
    try {
      const { blob, filename } = await fetchExport(jobId, apiKey);
      downloadBlob(blob, filename);
      setExportState({ busy: false, error: null, done: filename });
    } catch (e) {
      setExportState({
        busy: false,
        error: e instanceof ApiError ? e.message : "Не удалось скачать отчёт.",
        done: null,
      });
    }
  };

  const openMissed = () => {
    const t = currentTime();
    setFormError(t === null ? "Текущее время видео недоступно — укажите время вручную." : null);
    setForm({ time: t === null ? "" : t.toFixed(3), type: "", comment: "" });
  };

  const sendMissed = async () => {
    if (!form) return;
    const t = parseTimeInput(form.time);
    if (t === null) {
      setFormError("Укажите время пропущенного события в секундах (например 12.5) или мм:сс.");
      return;
    }
    setFormError(null);
    setFormBusy(true);
    try {
      await submit(MISSED_EVENT_ID, {
        decision: "missed",
        comment: form.comment.trim(),
        corrected_type: form.type.trim() || null,
        corrected_timecode_sec: t,
        reviewer: reviewer.trim(),
      });
      setForm(null);
    } catch (e) {
      setFormError(e instanceof ApiError ? e.message : "Не удалось отправить сообщение.");
    } finally {
      setFormBusy(false);
    }
  };

  return (
    <section className="card" aria-labelledby={`${id}-title`} data-testid="reviewer-toolbar">
      <h2 id={`${id}-title`}>Проверка сотрудником</h2>
      <div className="field">
        <label htmlFor={`${id}-name`}>Имя проверяющего</label>
        <input
          id={`${id}-name`}
          type="text"
          maxLength={80}
          autoComplete="off"
          value={reviewer}
          onChange={(e) => onReviewerChange(e.target.value)}
        />
        <p className="hint">Сохраняется только в этой вкладке и подставляется в решения.</p>
      </div>
      <div className="row">
        <button type="button" className="btn" onClick={openMissed} disabled={form !== null}>
          Сообщить о пропущенном событии
        </button>
        <button type="button" className="btn" onClick={() => void doExport()} disabled={exportState.busy}>
          {exportState.busy ? "Подготовка отчёта…" : "Скачать отчёт (JSON)"}
        </button>
      </div>
      {exportState.error && (
        <p className="notice notice-error" role="alert">
          {exportState.error}
        </p>
      )}
      {exportState.done && <p className="hint">Отчёт сохранён: {exportState.done}</p>}

      {form && (
        <form
          className="missed-form"
          aria-label="Пропущенное событие"
          onSubmit={(e) => {
            e.preventDefault();
            void sendMissed();
          }}
          noValidate
        >
          <div className="field-row">
            <div className="field">
              <label htmlFor={`${id}-mtime`}>Время события, с</label>
              <input
                id={`${id}-mtime`}
                type="text"
                inputMode="decimal"
                value={form.time}
                onChange={(e) => setForm({ ...form, time: e.target.value })}
                disabled={formBusy}
              />
            </div>
            <div className="field">
              <label htmlFor={`${id}-mtype`}>Тип нарушения (необязательно)</label>
              <input
                id={`${id}-mtype`}
                type="text"
                maxLength={200}
                value={form.type}
                onChange={(e) => setForm({ ...form, type: e.target.value })}
                disabled={formBusy}
              />
            </div>
          </div>
          <div className="field">
            <label htmlFor={`${id}-mcomment`}>Комментарий (необязательно)</label>
            <textarea
              id={`${id}-mcomment`}
              rows={2}
              maxLength={1000}
              value={form.comment}
              onChange={(e) => setForm({ ...form, comment: e.target.value })}
              disabled={formBusy}
            />
          </div>
          <div className="row">
            <button type="submit" className="btn btn-primary" disabled={formBusy}>
              {formBusy ? "Отправка…" : "Отправить"}
            </button>
            <button type="button" className="btn" onClick={() => setForm(null)} disabled={formBusy}>
              Отмена
            </button>
          </div>
        </form>
      )}
      {formError && (
        <p className="notice notice-error" role="alert">
          {formError}
        </p>
      )}

      {missed.length > 0 && (
        <div data-testid="missed-list">
          <h3 className="sub-title">Сообщения о пропущенных событиях ({missed.length})</h3>
          <ul className="missed-list">
            {missed.map((m) => (
              <li key={m.review_id}>
                <strong>{m.corrected_timecode_sec != null ? formatTimecode(m.corrected_timecode_sec) : "время не указано"}</strong>
                {m.corrected_type ? ` — ${m.corrected_type}` : ""}
                <span className="muted">
                  {" "}
                  ({m.reviewer?.trim() || "проверяющий не указан"}, {formatDateTime(m.reviewed_at)})
                </span>
                {m.comment && <div className="hint">{m.comment}</div>}
              </li>
            ))}
          </ul>
        </div>
      )}
    </section>
  );
}
