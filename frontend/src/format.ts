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

/** ISO-8601 -> local "17.10.2026, 13:00:00"; unparsable input is shown as-is. */
export function formatDateTime(iso: string | null | undefined): string {
  if (!iso) return "—";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleString("ru-RU");
}

/** Parse "12.5" / "12,5" / "01:02.500" into seconds; null if invalid or negative. */
export function parseTimeInput(text: string): number | null {
  const t = text.trim().replace(",", ".");
  if (!t) return null;
  let sec: number;
  const m = /^(\d+):(\d{1,2}(?:\.\d+)?)$/.exec(t);
  if (m) sec = Number(m[1]) * 60 + Number(m[2]);
  else if (/^\d+(?:\.\d+)?$/.test(t)) sec = Number(t);
  else return null;
  if (!Number.isFinite(sec) || sec < 0) return null;
  return Math.round(sec * 1000) / 1000;
}

/** Clip window around an event interval: [max(0, start-pad), end+pad] clamped to duration. */
export function clipWindow(startSec: number, endSec: number, durationSec?: number | null, padSec = 3): { start: number; end: number } {
  const start = Math.max(0, startSec - padSec);
  let end = Math.max(start, endSec + padSec);
  if (durationSec && Number.isFinite(durationSec) && durationSec > 0) end = Math.min(end, durationSec);
  return { start: Math.min(start, end), end };
}
