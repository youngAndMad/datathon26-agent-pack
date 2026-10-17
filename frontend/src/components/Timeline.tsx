import { formatSeconds, formatTimecode } from "../format";
import type { UiEvent } from "../types";

interface Props {
  events: UiEvent[];
  durationSec: number | null;
  onSeek: (event: UiEvent) => void;
}

export function Timeline({ events, durationSec, onSeek }: Props) {
  const fallback = Math.max(1, ...events.map((e) => Math.max(e.interval.end_sec, e.timecode_sec))) * 1.05;
  const duration = durationSec && Number.isFinite(durationSec) && durationSec > 0 ? durationSec : fallback;
  return (
    <div className="timeline-wrap">
      <p className="label" id="timeline-label">
        Шкала времени{durationSec ? ` (0:00 — ${formatTimecode(duration)})` : " (длительность видео неизвестна, масштаб приблизительный)"}
      </p>
      <div className="timeline" role="group" aria-labelledby="timeline-label">
        {events.map((ev, i) => {
          const left = Math.min(100, Math.max(0, (ev.timecode_sec / duration) * 100));
          const label = `Событие ${i + 1}: ${ev.type}, ${formatTimecode(ev.timecode_sec)}`;
          return (
            <button
              key={ev.event_id}
              type="button"
              className="marker"
              style={{ left: `${left}%` }}
              onClick={() => onSeek(ev)}
              aria-label={`Перейти к событию ${i + 1} (${formatTimecode(ev.timecode_sec)}, ${formatSeconds(ev.timecode_sec)})`}
              title={label}
              data-testid={`marker-${ev.event_id}`}
            >
              <span aria-hidden="true">{i + 1}</span>
            </button>
          );
        })}
      </div>
    </div>
  );
}
