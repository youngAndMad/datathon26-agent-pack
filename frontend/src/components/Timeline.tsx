import { formatSeconds, formatTimecode } from "../format";
import type { TimeInterval, UiEvent } from "../types";

interface Props {
  events: UiEvent[];
  unverifiable?: TimeInterval[];
  durationSec: number | null;
  onSeek: (event: UiEvent) => void;
  onSeekInterval?: (interval: TimeInterval) => void;
}

export function Timeline({ events, unverifiable = [], durationSec, onSeek, onSeekInterval }: Props) {
  const fallback =
    Math.max(
      1,
      ...events.map((e) => Math.max(e.interval.end_sec, e.timecode_sec)),
      ...unverifiable.map((iv) => iv.end_sec),
    ) * 1.05;
  const duration = durationSec && Number.isFinite(durationSec) && durationSec > 0 ? durationSec : fallback;
  const pct = (t: number) => Math.min(100, Math.max(0, (t / duration) * 100));
  return (
    <div className="timeline-wrap">
      <p className="label" id="timeline-label">
        Шкала времени{durationSec ? ` (0:00 — ${formatTimecode(duration)})` : " (длительность видео неизвестна, масштаб приблизительный)"}
      </p>
      <div className="timeline" role="group" aria-labelledby="timeline-label">
        {unverifiable.map((iv, i) => {
          const left = pct(iv.start_sec);
          const width = Math.max(0.8, pct(iv.end_sec) - left);
          return (
            <button
              key={`u-${i}`}
              type="button"
              className="seg-unverifiable"
              style={{ left: `${left}%`, width: `${width}%` }}
              onClick={() => onSeekInterval?.(iv)}
              aria-label={`Непроверяемый интервал ${formatTimecode(iv.start_sec)} — ${formatTimecode(iv.end_sec)}: перейти`}
              title={`Не удалось проверить: ${formatTimecode(iv.start_sec)} — ${formatTimecode(iv.end_sec)}`}
              data-testid={`seg-unverifiable-${i}`}
            />
          );
        })}
        {events.map((ev, i) => {
          const left = pct(ev.timecode_sec);
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
      {unverifiable.length > 0 && (
        <p className="hint legend">
          <span className="legend-swatch" aria-hidden="true" /> штриховка — интервалы, которые не удалось проверить по видео
        </p>
      )}
    </div>
  );
}
