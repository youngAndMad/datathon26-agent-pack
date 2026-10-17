import { forwardRef } from "react";
import { formatSeconds, formatTimecode } from "../format";
import type { UiEvent } from "../types";
import { EvidenceThumb } from "./EvidenceThumb";

function safeHttpUrl(url: string): string | null {
  try {
    const u = new URL(url);
    return u.protocol === "https:" || u.protocol === "http:" ? u.href : null;
  } catch {
    return null;
  }
}

interface Props {
  event: UiEvent;
  index: number;
  count: number;
  onSeek: (event: UiEvent) => void;
}

export const EventCard = forwardRef<HTMLElement, Props>(function EventCard({ event, index, count, onSeek }, ref) {
  const headingId = `event-${event.event_id}-title`;
  const link = safeHttpUrl(event.source.official_url);
  return (
    <article className="event" ref={ref} tabIndex={-1} aria-labelledby={headingId} data-testid={`event-${event.event_id}`}>
      <header className="event-head">
        <h3 id={headingId}>
          <span className="muted">
            Событие {index + 1} из {count}:{" "}
          </span>
          {event.type}
        </h3>
        <button type="button" className="btn btn-time" onClick={() => onSeek(event)}>
          <span aria-hidden="true">▶ </span>
          <time dateTime={`PT${event.timecode_sec.toFixed(3)}S`}>{formatTimecode(event.timecode_sec)}</time>{" "}
          ({formatSeconds(event.timecode_sec)})
          <span className="sr-only"> — перейти в видео</span>
        </button>
      </header>

      <section className="event-section">
        <h4>Что видно</h4>
        <p>{event.description}</p>
        <p className="hint">
          Интервал: {formatTimecode(event.interval.start_sec)} — {formatTimecode(event.interval.end_sec)}
        </p>
        {event.evidence.length > 0 && (
          <div className="thumbs">
            {event.evidence.slice(0, 3).map((ev, i) => (
              <EvidenceThumb key={`${ev.image_url}-${i}`} evidence={ev} />
            ))}
          </div>
        )}
      </section>

      <section className="event-section">
        <h4>Какое требование может быть затронуто</h4>
        <p>{event.type}</p>
        <p className="hint">Требует проверки сотрудником; это не окончательный вывод.</p>
      </section>

      <section className="event-section">
        <h4>Источник</h4>
        <p>
          {event.source.document_title}
          {event.source.clause ? `, п. ${event.source.clause}` : ""}
        </p>
        <blockquote className="excerpt">{event.source.local_excerpt}</blockquote>
        {link ? (
          <p>
            <a href={link} target="_blank" rel="noopener noreferrer">
              Официальный текст документа
            </a>
            <span className="sr-only"> (откроется в новой вкладке)</span>
          </p>
        ) : (
          <p className="hint">Ссылка на официальный источник недоступна.</p>
        )}
      </section>

      {event.limitations_ru.length > 0 && (
        <section className="event-section">
          <h4>Ограничения</h4>
          <ul>
            {event.limitations_ru.map((l, i) => (
              <li key={i}>{l}</li>
            ))}
          </ul>
        </section>
      )}
    </article>
  );
});
