/** 12.345 -> "00:12.345" (mm:ss.mmm). Minutes may exceed 59. */
export function formatTimecode(sec: number): string {
  if (!Number.isFinite(sec) || sec < 0) return "--:--.---";
  const totalMs = Math.round(sec * 1000);
  const minutes = Math.floor(totalMs / 60000);
  const seconds = Math.floor((totalMs % 60000) / 1000);
  const ms = totalMs % 1000;
  return `${String(minutes).padStart(2, "0")}:${String(seconds).padStart(2, "0")}.${String(ms).padStart(3, "0")}`;
}

/** 12.345 -> "12.345 с" (stored precision: 3 decimals, D007). */
export function formatSeconds(sec: number): string {
  return `${sec.toFixed(3)} с`;
}

/** Elapsed wall time -> "m:ss". */
export function formatElapsed(sec: number): string {
  const s = Math.max(0, Math.floor(sec));
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
}

export function formatBytes(bytes: number): string {
  const mb = bytes / (1024 * 1024);
  return `${mb.toLocaleString("ru-RU", { minimumFractionDigits: 1, maximumFractionDigits: 1 })} МБ`;
}

/** Seek target: slightly before the event, clamped to [0, duration]. */
export function seekTarget(timecodeSec: number, durationSec?: number, leadSec = 1): number {
  let t = Math.max(0, timecodeSec - leadSec);
  if (durationSec !== undefined && Number.isFinite(durationSec) && durationSec > 0) {
    t = Math.min(t, durationSec);
  }
  return t;
}

export function pluralEvents(n: number): string {
  const m10 = n % 10;
  const m100 = n % 100;
  if (m10 === 1 && m100 !== 11) return "событие";
  if (m10 >= 2 && m10 <= 4 && (m100 < 12 || m100 > 14)) return "события";
  return "событий";
}
