import { useId, useState } from "react";
import { ApiError } from "../api";
import { formatDateTime, formatTimecode, parseTimeInput } from "../format";
import { DECISION_RU, REVIEWER_DECISION_TITLE } from "../messages";
import type { EventReview, ReviewDecision, ReviewRequest } from "../types";

export function ReviewSummary({ review }: { review: EventReview }) {
  return (
    <div className={`review-latest review-${review.decision}`} data-testid={`review-latest-${review.event_id}`}>
      <p>
        <strong>{DECISION_RU[review.decision] ?? review.decision}</strong>
        <span className="muted">
          {" "}
          — {review.reviewer?.trim() || "проверяющий не указан"}, {formatDateTime(review.reviewed_at)}
        </span>
      </p>
      {review.corrected_type && <p className="hint">Исправленный тип: {review.corrected_type}</p>}
      {review.corrected_timecode_sec != null && (
        <p className="hint">Исправленное время: {formatTimecode(review.corrected_timecode_sec)}</p>
      )}
      {review.comment && <p className="hint">Комментарий: {review.comment}</p>}
    </div>
  );
}

interface Props {
  eventId: string;
  reviews: EventReview[]; // this event's reviews, oldest first
  reviewer: string;
  submit: (eventId: string, req: ReviewRequest) => Promise<void>;
  currentTime: () => number | null;
}

const DECISIONS: { value: Exclude<ReviewDecision, "missed">; label: string }[] = [
  { value: "confirmed", label: "Подтверждено" },
  { value: "rejected", label: "Отклонено" },
  { value: "needs_review", label: "Требует проверки" },
];

/** Human decision for one event. Shown separately from, and never altering, the system result. */
export function ReviewBox({ eventId, reviews, reviewer, submit, currentTime }: Props) {
  const id = useId();
  const [comment, setComment] = useState("");
  const [corrType, setCorrType] = useState("");
  const [corrTime, setCorrTime] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const latest = reviews.length > 0 ? reviews[reviews.length - 1] : null;

  const send = async (decision: ReviewDecision) => {
    setError(null);
    let t: number | null = null;
    if (corrTime.trim()) {
      t = parseTimeInput(corrTime);
      if (t === null) {
        setError("Исправленное время: укажите секунды (например 12.5) или мм:сс.");
        return;
      }
    }
    const req: ReviewRequest = {
      decision,
      comment: comment.trim(),
      reviewer: reviewer.trim(),
      corrected_type: corrType.trim() || null,
      corrected_timecode_sec: t,
    };
    setBusy(true);
    try {
      await submit(eventId, req);
      setComment("");
      setCorrType("");
      setCorrTime("");
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Не удалось сохранить решение.");
    } finally {
      setBusy(false);
    }
  };

  const fillTime = () => {
    const t = currentTime();
    if (t === null) {
      setError("Текущее время видео недоступно — введите время вручную.");
      return;
    }
    setError(null);
    setCorrTime(t.toFixed(3));
  };

  return (
    <section className="event-section review-box" aria-labelledby={`${id}-title`} data-testid={`review-${eventId}`}>
      <h4 id={`${id}-title`}>{REVIEWER_DECISION_TITLE}</h4>
      <p className="hint">Отдельно от результата системы: результат анализа выше не изменяется.</p>
      {latest ? (
        <>
          <ReviewSummary review={latest} />
          {reviews.length > 1 && <p className="hint">Всего решений по событию: {reviews.length}. Показано последнее.</p>}
        </>
      ) : (
        <p className="hint">Решение ещё не принято.</p>
      )}

      <div className="review-fields">
        <div className="field">
          <label htmlFor={`${id}-comment`}>Комментарий (необязательно)</label>
          <textarea
            id={`${id}-comment`}
            maxLength={1000}
            rows={2}
            value={comment}
            onChange={(e) => setComment(e.target.value)}
            disabled={busy}
          />
        </div>
        <div className="field-row">
          <div className="field">
            <label htmlFor={`${id}-type`}>Исправленный тип (необязательно)</label>
            <input
              id={`${id}-type`}
              type="text"
              maxLength={200}
              value={corrType}
              onChange={(e) => setCorrType(e.target.value)}
              disabled={busy}
            />
          </div>
          <div className="field">
            <label htmlFor={`${id}-time`}>Исправленное время, с (необязательно)</label>
            <div className="inline">
              <input
                id={`${id}-time`}
                type="text"
                inputMode="decimal"
                value={corrTime}
                onChange={(e) => setCorrTime(e.target.value)}
                disabled={busy}
              />
              <button type="button" className="btn btn-small" onClick={fillTime} disabled={busy}>
                = текущее время видео
              </button>
            </div>
          </div>
        </div>
      </div>

      <div className="row" role="group" aria-label="Решение проверяющего по событию">
        {DECISIONS.map((d) => (
          <button key={d.value} type="button" className="btn" disabled={busy} onClick={() => void send(d.value)}>
            {d.label}
          </button>
        ))}
      </div>
      {busy && <p className="hint">Сохранение…</p>}
      {error && (
        <p className="notice notice-error" role="alert">
          {error}
        </p>
      )}
    </section>
  );
}
