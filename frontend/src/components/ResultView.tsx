import { useEffect, useMemo, useRef, useState, type RefObject } from "react";
import { mediaPath } from "../api";
import { BlobRegistry, BlobRegistryContext, useAuthorizedObjectUrl } from "../blobs";
import { formatTimecode, pluralEvents, seekTarget } from "../format";
import { CLEAN_QUALIFICATION, VERDICT_CLEAN, VERDICT_VIOLATION } from "../messages";
import type { JobDone, UiEvent, UiReport } from "../types";
import { EventCard } from "./EventCard";
import { Timeline } from "./Timeline";

interface Props {
  headingRef: RefObject<HTMLHeadingElement | null>;
  apiKey: string;
  jobId: string;
  job: JobDone;
  report: UiReport;
  onReset: () => void;
}

export function ResultView({ headingRef, apiKey, jobId, job, report, onReset }: Props) {
  const registry = useMemo(() => new BlobRegistry(apiKey), [apiKey, jobId]);
  useEffect(() => () => registry.revokeAll(), [registry]);

  const events = report.events ?? [];
  const violation = job.verdict === "violation";
  const videoRef = useRef<HTMLVideoElement | null>(null);
  const cardRefs = useRef(new Map<string, HTMLElement>());
  const [duration, setDuration] = useState<number | null>(null);
  const [playable, setPlayable] = useState(false);
  const [seekNote, setSeekNote] = useState("");

  const seek = (ev: UiEvent) => {
    const v = videoRef.current;
    const target = seekTarget(ev.timecode_sec, duration ?? undefined);
    if (v && playable) {
      v.currentTime = target;
      v.scrollIntoView?.({ block: "nearest", behavior: "smooth" });
      setSeekNote(`Видео перемотано на ${formatTimecode(target)} (за секунду до события в ${formatTimecode(ev.timecode_sec)}).`);
    } else {
      const card = cardRefs.current.get(ev.event_id);
      card?.focus();
      card?.scrollIntoView?.({ block: "start", behavior: "smooth" });
      setSeekNote(`Видео недоступно. Показано событие в ${formatTimecode(ev.timecode_sec)}.`);
    }
  };

  const showCoverage =
    !!report.coverage_notice_ru && !(job.verdict === "no_violation" && report.coverage_notice_ru === CLEAN_QUALIFICATION);

  return (
    <BlobRegistryContext.Provider value={registry}>
      <section
        className={`card verdict ${violation ? "verdict-violation" : "verdict-clean"}`}
        aria-labelledby="verdict-title"
        data-testid="verdict"
      >
        <h2 id="verdict-title" ref={headingRef} tabIndex={-1}>
          <span aria-hidden="true">{violation ? "⚠ " : "✓ "}</span>
          {violation ? VERDICT_VIOLATION : VERDICT_CLEAN}
        </h2>
        {violation ? (
          <p className="verdict-sub">
            Найдено {events.length} {pluralEvents(events.length)}. Каждое событие требует проверки сотрудником.
          </p>
        ) : (
          <p className="verdict-sub">{CLEAN_QUALIFICATION}</p>
        )}
        {showCoverage && <p className="notice notice-info">Охват анализа: {report.coverage_notice_ru}</p>}
      </section>

      {(report.media_available || events.length > 0) && (
        <section className="card" aria-labelledby="video-title">
          <h2 id="video-title">Видео</h2>
          {report.media_available ? (
            <VideoPanel
              path={mediaPath(jobId)}
              videoRef={videoRef}
              onPlayable={setPlayable}
              onDuration={setDuration}
            />
          ) : (
            <p className="notice notice-info">Предпросмотр видео недоступен. Используйте кадры-доказательства ниже.</p>
          )}
          {events.length > 0 && <Timeline events={events} durationSec={duration} onSeek={seek} />}
          <p className="sr-only" aria-live="polite">
            {seekNote}
          </p>
        </section>
      )}

      {events.length > 0 && (
        <section aria-labelledby="events-title">
          <h2 id="events-title" className="section-title">
            События ({events.length})
          </h2>
          {events.map((ev, i) => (
            <EventCard
              key={ev.event_id}
              ref={(el) => {
                if (el) cardRefs.current.set(ev.event_id, el);
                else cardRefs.current.delete(ev.event_id);
              }}
              event={ev}
              index={i}
              count={events.length}
              onSeek={seek}
            />
          ))}
        </section>
      )}

      <div className="row">
        <button type="button" className="btn btn-primary" onClick={onReset}>
          Новый анализ
        </button>
      </div>
    </BlobRegistryContext.Provider>
  );
}

interface VideoProps {
  path: string;
  videoRef: RefObject<HTMLVideoElement | null>;
  onPlayable: (ok: boolean) => void;
  onDuration: (d: number | null) => void;
}

function VideoPanel({ path, videoRef, onPlayable, onDuration }: VideoProps) {
  const state = useAuthorizedObjectUrl(path);
  const [broken, setBroken] = useState(false);

  useEffect(() => {
    onPlayable(state.status === "ok" && !broken);
  }, [state.status, broken, onPlayable]);

  if (state.status === "loading") return <p className="hint">Загрузка видео…</p>;
  if (state.status === "error") {
    return <p className="notice notice-warn">Не удалось загрузить видео. Используйте кадры-доказательства ниже.</p>;
  }
  return (
    <>
      {broken ? (
        <p className="notice notice-warn" data-testid="video-fallback">
          Браузер не может воспроизвести это видео. Используйте кадры-доказательства ниже или скачайте файл.
        </p>
      ) : (
        <video
          ref={videoRef}
          className="video"
          src={state.url}
          controls
          preload="metadata"
          playsInline
          muted
          data-testid="video"
          onError={() => setBroken(true)}
          onLoadedMetadata={(e) => {
            const d = e.currentTarget.duration;
            onDuration(Number.isFinite(d) && d > 0 ? d : null);
          }}
        >
          Браузер не поддерживает воспроизведение видео.
        </video>
      )}
      <p>
        <a href={state.url} download="preview">
          Скачать видео
        </a>
      </p>
    </>
  );
}
