import { useEffect, useMemo, useRef, useState, type RefObject } from "react";
import { MISSED_EVENT_ID, mediaPath, postReview } from "../api";
import { BlobRegistry, BlobRegistryContext, useAuthorizedObjectUrl } from "../blobs";
import { clipWindow, formatTimecode, pluralEvents, seekTarget } from "../format";
import { CLEAN_QUALIFICATION, VERDICT_CLEAN, VERDICT_VIOLATION } from "../messages";
import type { EventReview, JobDone, ReviewRequest, TimeInterval, UiEvent, UiReport } from "../types";
import { EventCard } from "./EventCard";
import { Timeline } from "./Timeline";
import { ObservabilityPanel } from "./ObservabilityPanel";
import { ReviewBox } from "./ReviewBox";
import { ReviewerToolbar } from "./ReviewerToolbar";

interface Props {
  headingRef: RefObject<HTMLHeadingElement | null>;
  apiKey: string;
  jobId: string;
  job: JobDone;
  report: UiReport;
  onReset: () => void;
  onBackToHistory?: () => void;
}

const REVIEWER_STORAGE_KEY = "cashwatch.reviewer";

function readReviewer(): string {
  try {
    return window.sessionStorage.getItem(REVIEWER_STORAGE_KEY) ?? "";
  } catch {
    return "";
  }
}
function storeReviewer(name: string): void {
  try {
    if (name) window.sessionStorage.setItem(REVIEWER_STORAGE_KEY, name);
    else window.sessionStorage.removeItem(REVIEWER_STORAGE_KEY);
  } catch {
    /* optional convenience only */
  }
}

function safePlay(v: HTMLVideoElement): void {
  try {
    const p = v.play() as Promise<void> | undefined;
    p?.catch?.(() => undefined);
  } catch {
    /* autoplay refused / not implemented: the user can press play */
  }
}

interface ClipState {
  eventId: string;
  start: number;
  end: number;
}

export function ResultView({ headingRef, apiKey, jobId, job, report, onReset, onBackToHistory }: Props) {
  const registry = useMemo(() => new BlobRegistry(apiKey), [apiKey, jobId]);
  useEffect(() => () => registry.revokeAll(), [registry]);

  const events = report.events ?? [];
  const ruleCoverage = report.rule_coverage ?? [];
  const unverifiable = useMemo(
    () => ruleCoverage.flatMap((r) => r.unverifiable_intervals ?? []).sort((a, b) => a.start_sec - b.start_sec),
    [ruleCoverage],
  );
  const violation = job.verdict === "violation";
  const videoRef = useRef<HTMLVideoElement | null>(null);
  const cardRefs = useRef(new Map<string, HTMLElement>());
  const [duration, setDuration] = useState<number | null>(null);
  const [playable, setPlayable] = useState(false);
  const [seekNote, setSeekNote] = useState("");
  const [clip, setClip] = useState<ClipState | null>(null);
  const [loopClip, setLoopClip] = useState(false);
  const [reviews, setReviews] = useState<EventReview[]>(() => report.reviews ?? []);
  const [reviewer, setReviewer] = useState(readReviewer);

  const seekTo = (t: number, note: string) => {
    const v = videoRef.current;
    setClip(null);
    if (v && playable) {
      v.currentTime = t;
      v.scrollIntoView?.({ block: "nearest", behavior: "smooth" });
      setSeekNote(note);
      return true;
    }
    return false;
  };

  const seek = (ev: UiEvent) => {
    const target = seekTarget(ev.timecode_sec, duration ?? undefined);
    const ok = seekTo(
      target,
      `Видео перемотано на ${formatTimecode(target)} (за секунду до события в ${formatTimecode(ev.timecode_sec)}).`,
    );
    if (!ok) {
      const card = cardRefs.current.get(ev.event_id);
      card?.focus();
      card?.scrollIntoView?.({ block: "start", behavior: "smooth" });
      setSeekNote(`Видео недоступно. Показано событие в ${formatTimecode(ev.timecode_sec)}.`);
    }
  };

  const seekInterval = (iv: TimeInterval) => {
    const target = seekTarget(iv.start_sec, duration ?? undefined, 0);
    const ok = seekTo(
      target,
      `Видео перемотано на начало непроверяемого интервала ${formatTimecode(iv.start_sec)} — ${formatTimecode(iv.end_sec)}.`,
    );
    if (!ok) setSeekNote(`Видео недоступно. Интервал ${formatTimecode(iv.start_sec)} — ${formatTimecode(iv.end_sec)}.`);
  };

  const playClip = (ev: UiEvent) => {
    const v = videoRef.current;
    if (!v || !playable) {
      setSeekNote("Видео недоступно — фрагмент нельзя воспроизвести. Используйте кадры-доказательства.");
      return;
    }
    const w = clipWindow(ev.interval.start_sec, ev.interval.end_sec, duration);
    v.currentTime = w.start;
    setClip({ eventId: ev.event_id, start: w.start, end: w.end });
    v.scrollIntoView?.({ block: "nearest", behavior: "smooth" });
    safePlay(v);
    setSeekNote(`Воспроизводится фрагмент ${formatTimecode(w.start)} — ${formatTimecode(w.end)}.`);
  };

  const onTimeUpdate = () => {
    const v = videoRef.current;
    if (!v || !clip) return;
    if (v.currentTime >= clip.end) {
      if (loopClip) {
        v.currentTime = clip.start;
      } else {
        v.pause();
        setClip(null);
        setSeekNote("Фрагмент завершён.");
      }
    }
  };

  const currentTime = (): number | null => {
    const v = videoRef.current;
    if (!v || !playable || !Number.isFinite(v.currentTime)) return null;
    return Math.round(v.currentTime * 1000) / 1000;
  };

  const submitReview = async (eventId: string, req: ReviewRequest) => {
    const r = await postReview(jobId, eventId, req, apiKey);
    setReviews((prev) => [...prev, r]);
  };

  const reviewsFor = (eventId: string) => reviews.filter((r) => r.event_id === eventId);
  const missed = reviews.filter((r) => r.event_id === MISSED_EVENT_ID || r.decision === "missed");

  const showCoverage =
    !!report.coverage_notice_ru && !(job.verdict === "no_violation" && report.coverage_notice_ru === CLEAN_QUALIFICATION);
  const showVideo = report.media_available || events.length > 0 || unverifiable.length > 0;

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
        <p className="hint">Результат системы. Решения проверяющего хранятся отдельно и его не изменяют.</p>
      </section>

      <ReviewerToolbar
        jobId={jobId}
        apiKey={apiKey}
        reviewer={reviewer}
        onReviewerChange={(name) => {
          setReviewer(name);
          storeReviewer(name.trim());
        }}
        missed={missed}
        submit={submitReview}
        currentTime={currentTime}
      />

      <ObservabilityPanel
        ruleCoverage={ruleCoverage}
        qualityNotes={report.quality_notes_ru ?? []}
        reviewRequired={!!report.review_required}
        clean={!violation}
        onSeekInterval={seekInterval}
      />

      {showVideo && (
        <section className="card" aria-labelledby="video-title">
          <h2 id="video-title">Видео</h2>
          {report.media_available ? (
            <VideoPanel
              path={mediaPath(jobId)}
              videoRef={videoRef}
              onPlayable={setPlayable}
              onDuration={setDuration}
              onTimeUpdate={onTimeUpdate}
            />
          ) : (
            <p className="notice notice-info">Предпросмотр видео недоступен. Используйте кадры-доказательства ниже.</p>
          )}
          {events.length > 0 && playable && (
            <label className="checkbox">
              <input type="checkbox" checked={loopClip} onChange={(e) => setLoopClip(e.target.checked)} /> Повторять
              фрагмент события
            </label>
          )}
          {(events.length > 0 || unverifiable.length > 0) && (
            <Timeline
              events={events}
              unverifiable={unverifiable}
              durationSec={duration}
              onSeek={seek}
              onSeekInterval={seekInterval}
            />
          )}
          <p className="hint" aria-live="polite" data-testid="seek-note">
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
              onClip={playClip}
              clipAvailable={playable}
              clipActive={clip?.eventId === ev.event_id}
            >
              <ReviewBox
                eventId={ev.event_id}
                reviews={reviewsFor(ev.event_id)}
                reviewer={reviewer}
                submit={submitReview}
                currentTime={currentTime}
              />
            </EventCard>
          ))}
        </section>
      )}

      <div className="row">
        <button type="button" className="btn btn-primary" onClick={onReset}>
          Новый анализ
        </button>
        {onBackToHistory && (
          <button type="button" className="btn" onClick={onBackToHistory}>
            К истории проверок
          </button>
        )}
      </div>
    </BlobRegistryContext.Provider>
  );
}

interface VideoProps {
  path: string;
  videoRef: RefObject<HTMLVideoElement | null>;
  onPlayable: (ok: boolean) => void;
  onDuration: (d: number | null) => void;
  onTimeUpdate: () => void;
}

function VideoPanel({ path, videoRef, onPlayable, onDuration, onTimeUpdate }: VideoProps) {
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
          onTimeUpdate={onTimeUpdate}
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
