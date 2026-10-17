import { beforeEach, describe, expect, it, vi } from "vitest";
import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import App, { JOB_STORAGE_KEY } from "../App";
import { filenameFromDisposition, isApiPath } from "../api";
import { clipWindow, parseTimeInput } from "../format";
import cleanReport from "../mock/fixtures/ui-report-no-violation.json";
import juryClean from "../mock/fixtures/jury-no-violation.json";
import failedReport from "../mock/fixtures/ui-report-failed.json";
import jobsList from "../mock/fixtures/jobs-list.json";
import { FakeXHR, JOB, KEY, installFetch, installXHR, json, juryDone, movFile, violationReport } from "./helpers";

const CONFIG = { pollIntervalMs: 5, maxBackoffMs: 20, maxConsecutiveFailures: 1000 };
const JOB2 = "00000000-0000-4000-8000-000000000002";
const playableMedia = () => new Response(new Blob([new Uint8Array(8)], { type: "video/mp4" }));

type User = ReturnType<typeof userEvent.setup>;

async function openDone(user: User) {
  await user.type(screen.getByLabelText("Ключ доступа"), KEY);
  await user.upload(screen.getByTestId("file-input"), movFile());
  await user.click(screen.getByRole("button", { name: "Анализировать" }));
  await waitFor(() => expect(FakeXHR.instances.length).toBeGreaterThan(0));
  act(() => FakeXHR.instances[FakeXHR.instances.length - 1].respond(202, { job_id: JOB }));
  await screen.findByTestId("verdict");
}

function echoReview(url: string, init?: RequestInit): Response | undefined {
  const m = /\/events\/([^/]+)\/review$/.exec(url);
  if (!m || init?.method !== "POST") return undefined;
  const body = JSON.parse(String(init.body));
  return json(
    {
      review_id: `r-${m[1]}-${Math.random().toString(36).slice(2, 6)}`,
      event_id: m[1],
      decision: body.decision,
      comment: body.comment,
      corrected_type: body.corrected_type ?? null,
      corrected_timecode_sec: body.corrected_timecode_sec ?? null,
      reviewer: body.reviewer,
      reviewed_at: "2026-10-17T11:00:00.000+00:00",
    },
    201,
  );
}

let play: ReturnType<typeof vi.fn>;
let pause: ReturnType<typeof vi.fn>;
beforeEach(() => {
  installXHR();
  play = vi.fn(() => Promise.resolve());
  pause = vi.fn();
  Object.defineProperty(HTMLMediaElement.prototype, "play", { configurable: true, value: play });
  Object.defineProperty(HTMLMediaElement.prototype, "pause", { configurable: true, value: pause });
});

describe("observability panel", () => {
  it("renders three rule statuses with text, quality notes, review badge and seeks on interval click", async () => {
    installFetch({ jobs: [juryDone()], report: [json(violationReport)], media: playableMedia });
    const user = userEvent.setup();
    render(<App config={CONFIG} />);
    await openDone(user);
    const panel = screen.getByTestId("observability");
    expect(within(panel).getByRole("heading", { name: "Проверенные правила" })).toBeTruthy();
    expect(within(screen.getByTestId("rule-FIXTURE-A")).getByText("проверено")).toBeTruthy();
    expect(within(screen.getByTestId("rule-FIXTURE-B")).getByText("проверено частично")).toBeTruthy();
    expect(screen.getByTestId("rule-FIXTURE-B").textContent).toContain("Зона работы с наличными перекрыта");
    expect(within(screen.getByTestId("rule-FIXTURE-C")).getByText("нельзя проверить по видео")).toBeTruthy();
    expect(within(panel).getByRole("heading", { name: "Качество записи" })).toBeTruthy();
    expect(panel.textContent).toContain("Часть кадров (3 из 120) слишком тёмная");
    expect(screen.getByTestId("review-required").textContent).toContain("Требуется просмотр человеком");
    // Badge does not replace the system verdict.
    expect(screen.getByTestId("verdict").textContent).toContain("Обнаружено возможное нарушение");

    const video = (await screen.findByTestId("video")) as HTMLVideoElement;
    await user.click(within(panel).getByRole("button", { name: /непроверяемому интервалу 01:00\.000 — 01:12\.000/ }));
    expect(video.currentTime).toBeCloseTo(60, 3);
    video.currentTime = 0;
    // Hatched segment on the timeline seeks too.
    await user.click(screen.getByTestId("seg-unverifiable-0"));
    expect(video.currentTime).toBeCloseTo(60, 3);
  });

  it("for no_violation shows the qualification and which rules could not be checked", async () => {
    const report = {
      ...cleanReport,
      rule_coverage: [
        { rule_id: "FIXTURE-A", type_ru: "Тестовый тип FIXTURE-A", status: "checked", unverifiable_intervals: [], note_ru: "" },
        { rule_id: "FIXTURE-C", type_ru: "Тестовый тип FIXTURE-C", status: "not_checkable", unverifiable_intervals: [], note_ru: "Нужная зона не видна в кадре" },
      ],
      review_required: true,
    };
    installFetch({ jobs: [json(juryClean)], report: [json(report)] });
    const user = userEvent.setup();
    render(<App config={CONFIG} />);
    await openDone(user);
    expect(screen.getByText("Нарушение не обнаружено")).toBeTruthy();
    expect(screen.getByText("В пределах видимых действий и проверяемых правил")).toBeTruthy();
    expect(screen.getByTestId("clean-limits").textContent).toContain("Тестовый тип FIXTURE-C");
    expect(screen.getByTestId("clean-limits").textContent).not.toContain("FIXTURE-A");
    expect(screen.getByTestId("review-required")).toBeTruthy();
  });
});

describe("event clip", () => {
  it("seeks to start-3 s, plays, and stops at end+3 s (or loops when enabled)", async () => {
    installFetch({ jobs: [juryDone()], report: [json(violationReport)], media: playableMedia });
    const user = userEvent.setup();
    render(<App config={CONFIG} />);
    await openDone(user);
    const video = (await screen.findByTestId("video")) as HTMLVideoElement;
    const card = screen.getByTestId("event-e1");
    await user.click(within(card).getByRole("button", { name: "Показать фрагмент" }));
    expect(video.currentTime).toBeCloseTo(7.345, 3); // 10.345 - 3
    expect(play).toHaveBeenCalled();
    expect(within(card).getByRole("button", { name: "Фрагмент воспроизводится…" })).toBeTruthy();

    video.currentTime = 12;
    fireEvent.timeUpdate(video);
    expect(pause).not.toHaveBeenCalled();
    video.currentTime = 18.4; // >= 15.345 + 3
    fireEvent.timeUpdate(video);
    expect(pause).toHaveBeenCalledTimes(1);
    expect(within(card).getByRole("button", { name: "Показать фрагмент" })).toBeTruthy();

    // Loop mode: jumps back to the clip start instead of stopping.
    await user.click(screen.getByLabelText(/Повторять/));
    await user.click(within(card).getByRole("button", { name: "Показать фрагмент" }));
    video.currentTime = 18.5;
    fireEvent.timeUpdate(video);
    expect(video.currentTime).toBeCloseTo(7.345, 3);
    expect(pause).toHaveBeenCalledTimes(1);

    // Time button still seeks to t-1 and cancels the clip.
    await user.click(within(card).getByRole("button", { name: /00:12\.345.*перейти в видео/ }));
    expect(video.currentTime).toBeCloseTo(11.345, 3);
    expect(within(card).getByRole("button", { name: "Показать фрагмент" })).toBeTruthy();
  });

  it("is disabled with an evidence-only hint when media cannot play", async () => {
    installFetch({ jobs: [juryDone()], report: [json(violationReport)], media: playableMedia });
    const user = userEvent.setup();
    render(<App config={CONFIG} />);
    await openDone(user);
    fireEvent.error(await screen.findByTestId("video"));
    await screen.findByTestId("video-fallback");
    const btn = within(screen.getByTestId("event-e1")).getByRole("button", { name: "Показать фрагмент" }) as HTMLButtonElement;
    expect(btn.disabled).toBe(true);
    expect(screen.getByTestId("event-e1").textContent).toContain("используйте кадры-доказательства");
  });
});

describe("reviewer decisions", () => {
  it("POSTs the decision with key header and shows it separately without changing the system result", async () => {
    const f = installFetch({ jobs: [juryDone()], report: [json(violationReport)], media: playableMedia, extra: echoReview });
    const user = userEvent.setup();
    render(<App config={CONFIG} />);
    await openDone(user);
    const video = (await screen.findByTestId("video")) as HTMLVideoElement;

    // Fixture review on e1 is shown as the latest decision.
    expect(screen.getByTestId("review-latest-e1").textContent).toMatch(/Подтверждено.*fixture/);

    await user.type(screen.getByLabelText("Имя проверяющего"), "Иванова");
    const box = screen.getByTestId("review-e2");
    await user.type(within(box).getByLabelText(/Комментарий/), "Не видно рук");
    await user.type(within(box).getByLabelText(/Исправленный тип/), "Другой тип");
    video.currentTime = 46.25;
    await user.click(within(box).getByRole("button", { name: "= текущее время видео" }));
    expect((within(box).getByLabelText(/Исправленное время/) as HTMLInputElement).value).toBe("46.250");
    await user.click(within(box).getByRole("button", { name: "Отклонено" }));

    const latest = await screen.findByTestId("review-latest-e2");
    const post = f.calls.find((c) => c.method === "POST");
    expect(post?.url).toBe(`/api/v1/jobs/${JOB}/events/e2/review`);
    expect(post?.key).toBe(KEY);
    expect(JSON.parse(post!.body!)).toEqual({
      decision: "rejected",
      comment: "Не видно рук",
      reviewer: "Иванова",
      corrected_type: "Другой тип",
      corrected_timecode_sec: 46.25,
    });
    expect(latest.textContent).toContain("Отклонено");
    expect(latest.textContent).toContain("Иванова");
    expect(latest.textContent).toContain("Другой тип");
    expect(within(box).getByRole("heading", { name: "Решение проверяющего" })).toBeTruthy();

    // System result untouched.
    expect(screen.getByTestId("verdict").textContent).toContain("Обнаружено возможное нарушение");
    expect(screen.getByText(/Найдено 2 события/)).toBeTruthy();
    const e2 = screen.getByTestId("event-e2");
    expect(within(e2).getByRole("heading", { level: 3 }).textContent).toContain("Тестовый тип FIXTURE-B");
    expect(e2.textContent).toContain("00:47.000");
  });

  it("shows server errors inline", async () => {
    installFetch({
      jobs: [juryDone()],
      report: [json(violationReport)],
      extra: (_url, init) =>
        init?.method === "POST" ? json({ error: { code: "not_done", message: "x" } }, 409) : undefined,
    });
    const user = userEvent.setup();
    render(<App config={CONFIG} />);
    await openDone(user);
    const box = screen.getByTestId("review-e1");
    await user.click(within(box).getByRole("button", { name: "Требует проверки" }));
    expect((await within(box).findByRole("alert")).textContent).toContain("Задание ещё не завершено");
    expect(screen.getByTestId("review-latest-e1").textContent).toContain("Подтверждено"); // unchanged
  });

  it("reports a missed event at the current video time and lists it", async () => {
    const f = installFetch({ jobs: [json(juryClean)], report: [json(cleanReport)], media: playableMedia, extra: echoReview });
    const user = userEvent.setup();
    render(<App config={CONFIG} />);
    await openDone(user);
    const video = (await screen.findByTestId("video")) as HTMLVideoElement;
    video.currentTime = 33.5;
    await user.click(screen.getByRole("button", { name: "Сообщить о пропущенном событии" }));
    const form = screen.getByRole("form", { name: "Пропущенное событие" });
    expect((within(form).getByLabelText(/Время события/) as HTMLInputElement).value).toBe("33.500");
    await user.type(within(form).getByLabelText(/Комментарий/), "Пересчёт вне зоны");
    await user.click(within(form).getByRole("button", { name: "Отправить" }));

    const list = await screen.findByTestId("missed-list");
    expect(list.textContent).toContain("00:33.500");
    expect(list.textContent).toContain("Пересчёт вне зоны");
    const post = f.calls.find((c) => c.method === "POST")!;
    expect(post.url).toBe(`/api/v1/jobs/${JOB}/events/missed/review`);
    expect(JSON.parse(post.body!)).toMatchObject({ decision: "missed", corrected_timecode_sec: 33.5 });
    expect(screen.getByText("Нарушение не обнаружено")).toBeTruthy();
  });
});

describe("export", () => {
  it("downloads the export via authorized fetch with the Content-Disposition filename", async () => {
    let disposition: string | null = 'attachment; filename="cashwatch-report-1.json"';
    const f = installFetch({
      jobs: [juryDone()],
      report: [json(violationReport)],
      extra: (url) =>
        url.endsWith("/export")
          ? new Response(JSON.stringify({ job_id: JOB }), {
              headers: { "Content-Type": "application/json", ...(disposition ? { "Content-Disposition": disposition } : {}) },
            })
          : undefined,
    });
    const downloads: string[] = [];
    vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(function (this: HTMLAnchorElement) {
      downloads.push(`${this.download}|${this.href}`);
    });
    const user = userEvent.setup();
    render(<App config={CONFIG} />);
    await openDone(user);
    await user.click(screen.getByRole("button", { name: "Скачать отчёт (JSON)" }));
    await waitFor(() => expect(downloads.length).toBe(1));
    expect(downloads[0]).toMatch(/^cashwatch-report-1\.json\|blob:/);
    const call = f.calls.find((c) => c.url.endsWith("/export"))!;
    expect(call.url).toBe(`/api/v1/jobs/${JOB}/export`);
    expect(call.key).toBe(KEY);
    expect(call.url).not.toContain(KEY);

    disposition = null;
    await user.click(screen.getByRole("button", { name: "Скачать отчёт (JSON)" }));
    await waitFor(() => expect(downloads.length).toBe(2));
    expect(downloads[1].split("|")[0]).toBe(`cashwatch-${JOB}.json`);
  });
});

describe("history", () => {
  it("lists jobs, filters by status and opens done and failed jobs", async () => {
    const f = installFetch({
      jobs: [juryDone()],
      report: [json(violationReport)],
      extra: (url) => {
        if (url.startsWith("/api/v1/jobs?")) {
          const status = new URLSearchParams(url.split("?")[1]).get("status");
          return json({ jobs: jobsList.jobs.filter((j) => !status || j.status === status) });
        }
        if (url === `/api/v1/jobs/${JOB2}`) return json({ job_id: JOB2, status: "failed", error: failedReport.error });
        if (url === `/api/v1/jobs/${JOB2}/report`) return json({ ...failedReport, job_id: JOB2 });
        return undefined;
      },
    });
    const user = userEvent.setup();
    render(<App config={CONFIG} />);
    const histBtn = screen.getByRole("button", { name: "История проверок" }) as HTMLButtonElement;
    expect(histBtn.disabled).toBe(true); // needs the key first
    await user.type(screen.getByLabelText("Ключ доступа"), KEY);
    await user.click(histBtn);

    const r1 = await screen.findByTestId(`history-row-${JOB}`);
    const r2 = screen.getByTestId(`history-row-${JOB2}`);
    expect(r1.textContent).toContain("Завершено");
    expect(r1.textContent).toContain("Возможное нарушение");
    expect(r1.textContent).toContain("1 из 2");
    expect(r1.textContent).toContain("Требуется просмотр человеком");
    expect(r1.textContent).toContain("ТЕСТ");
    expect(r2.textContent).toContain("Ошибка");
    expect(r2.textContent).toContain("Анализ не уложился в отведённое время");
    const listCall = f.calls.find((c) => c.url.startsWith("/api/v1/jobs?"))!;
    expect(listCall.url).toBe("/api/v1/jobs?limit=50");
    expect(listCall.key).toBe(KEY);

    await user.selectOptions(screen.getByLabelText("Статус"), "failed");
    await waitFor(() => expect(screen.queryByTestId(`history-row-${JOB}`)).toBeNull());
    expect(f.calls.some((c) => c.url === "/api/v1/jobs?limit=50&status=failed")).toBe(true);
    expect(screen.getByTestId(`history-row-${JOB2}`)).toBeTruthy();

    // Open the failed job: failed view, no "retry same file" (no file chosen for it).
    await user.click(screen.getByRole("button", { name: `Открыть проверку ${JOB2}` }));
    const failed = await screen.findByTestId("failed-view");
    expect(failed.textContent).toContain("Анализ не уложился в отведённое время");
    expect(within(failed).queryByRole("button", { name: "Повторить анализ" })).toBeNull();

    // Back to history, open the done job: result screen.
    await user.click(within(failed).getByRole("button", { name: "К истории проверок" }));
    await user.selectOptions(await screen.findByLabelText("Статус"), "");
    await user.click(await screen.findByRole("button", { name: `Открыть проверку ${JOB}` }));
    expect(await screen.findByText("Обнаружено возможное нарушение")).toBeTruthy();
    expect(screen.getByRole("button", { name: "К истории проверок" })).toBeTruthy();
  });

  it("history 401 returns to key entry", async () => {
    installFetch({
      jobs: [juryDone()],
      extra: (url) => (url.startsWith("/api/v1/jobs?") ? json({ error: { code: "unauthorized", message: "x" } }, 401) : undefined),
    });
    const user = userEvent.setup();
    render(<App config={CONFIG} />);
    await user.type(screen.getByLabelText("Ключ доступа"), KEY);
    await user.click(screen.getByRole("button", { name: "История проверок" }));
    expect((await screen.findByRole("alert")).textContent).toMatch(/Неверный ключ/);
    expect(screen.getByLabelText("Ключ доступа")).toBeTruthy();
  });
});

describe("secrets with reviews/export/history", () => {
  it("never writes the key to storage; only job id and reviewer name are stored", async () => {
    const setItem = vi.spyOn(Storage.prototype, "setItem");
    vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => undefined);
    const f = installFetch({
      jobs: [juryDone()],
      report: [json(violationReport)],
      extra: (url, init) =>
        echoReview(url, init) ??
        (url.endsWith("/export") ? json({ job_id: JOB }) : url.startsWith("/api/v1/jobs?") ? json(jobsList) : undefined),
    });
    const user = userEvent.setup();
    render(<App config={CONFIG} />);
    await openDone(user);
    await user.type(screen.getByLabelText("Имя проверяющего"), "Петров");
    await user.click(within(screen.getByTestId("review-e1")).getByRole("button", { name: "Подтверждено" }));
    await waitFor(() => expect(f.calls.some((c) => c.method === "POST")).toBe(true));
    await user.click(screen.getByRole("button", { name: "Скачать отчёт (JSON)" }));
    await waitFor(() => expect(f.calls.some((c) => c.url.endsWith("/export"))).toBe(true));
    await user.click(screen.getByRole("button", { name: "Новый анализ" }));
    await user.click(screen.getByRole("button", { name: "История проверок" }));
    await screen.findByTestId(`history-row-${JOB}`);

    for (const call of setItem.mock.calls) expect(call.join("|")).not.toContain(KEY);
    expect(new Set(setItem.mock.calls.map((c) => c[0]))).toEqual(new Set([JOB_STORAGE_KEY, "cashwatch.reviewer"]));
    expect(window.sessionStorage.getItem("cashwatch.reviewer")).toBe("Петров");
    expect(JSON.stringify({ ...window.localStorage })).not.toContain(KEY);
    expect(JSON.stringify({ ...window.sessionStorage })).not.toContain(KEY);
    expect(document.cookie).not.toContain(KEY);
    expect(window.location.href).not.toContain(KEY);
    expect(f.calls.every((c) => !c.url.includes(KEY) && c.key === KEY)).toBe(true);
    expect(f.calls.filter((c) => c.body).every((c) => !c.body!.includes(KEY))).toBe(true);
  });
});

describe("new helpers", () => {
  it("parses filenames, time input, clip windows and query paths", () => {
    expect(filenameFromDisposition('attachment; filename="a.json"', "f.json")).toBe("a.json");
    expect(filenameFromDisposition("attachment; filename*=UTF-8''%D0%BE%D1%82%D1%87%D1%91%D1%82.json", "f.json")).toBe("отчёт.json");
    expect(filenameFromDisposition('attachment; filename="../../etc/x"', "f.json")).toBe("_.._etc_x");
    expect(filenameFromDisposition(null, "f.json")).toBe("f.json");
    expect(parseTimeInput("12,5")).toBe(12.5);
    expect(parseTimeInput("01:02.5")).toBe(62.5);
    expect(parseTimeInput("-1")).toBeNull();
    expect(parseTimeInput("abc")).toBeNull();
    expect(clipWindow(1, 5)).toEqual({ start: 0, end: 8 });
    expect(clipWindow(10, 15, 16)).toEqual({ start: 7, end: 16 });
    expect(isApiPath("/api/v1/jobs?limit=50&status=done")).toBe(true);
    expect(isApiPath("/api/v1/jobs?x=https://evil")).toBe(false);
  });
});
