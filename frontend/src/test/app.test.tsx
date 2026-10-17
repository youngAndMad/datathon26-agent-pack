import { describe, expect, it, vi, beforeEach } from "vitest";
import { act, render, screen, waitFor, within, fireEvent } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import App, { JOB_STORAGE_KEY } from "../App";
import failedReport from "../mock/fixtures/ui-report-failed.json";
import cleanReport from "../mock/fixtures/ui-report-no-violation.json";
import juryClean from "../mock/fixtures/jury-no-violation.json";
import processingReport from "../mock/fixtures/ui-report-processing.json";
import {
  FakeXHR,
  JOB,
  KEY,
  installFetch,
  installXHR,
  json,
  juryDone,
  movFile,
  processing,
  violationReport,
} from "./helpers";

const CONFIG = { pollIntervalMs: 5, maxBackoffMs: 20, maxConsecutiveFailures: 1000 };

async function submit(user: ReturnType<typeof userEvent.setup>, file = movFile()) {
  await user.type(screen.getByLabelText("Ключ доступа"), KEY);
  await user.upload(screen.getByTestId("file-input"), file);
  await user.click(screen.getByRole("button", { name: "Анализировать" }));
  await waitFor(() => expect(FakeXHR.instances.length).toBeGreaterThan(0));
  return FakeXHR.instances[FakeXHR.instances.length - 1];
}

async function submitAccepted(user: ReturnType<typeof userEvent.setup>, file = movFile()) {
  const xhr = await submit(user, file);
  act(() => xhr.respond(202, { job_id: JOB }));
  return xhr;
}

beforeEach(() => {
  installXHR();
});

describe("upload", () => {
  it("sends multipart `file` with the key header, shows real byte progress and blocks duplicate submit", async () => {
    installFetch({ jobs: [processing()], report: [json(processingReport)] });
    const user = userEvent.setup();
    render(<App config={CONFIG} />);
    const file = movFile("clip.mov", 2048);
    const xhr = await submit(user, file);

    expect(xhr.method).toBe("POST");
    expect(xhr.url).toBe("/api/v1/analyze");
    expect(xhr.headers["X-API-Key"]).toBe(KEY);
    expect((xhr.body!.get("file") as File).name).toBe("clip.mov");

    act(() => xhr.progress(1024, 2048));
    expect(screen.getByText(/из .*\(50%\)/)).toBeTruthy();
    const progress = document.getElementById("upload-progress") as HTMLProgressElement;
    expect(progress.value).toBe(1024);

    // Duplicate submission impossible while uploading.
    const btn = screen.getByRole("button", { name: "Отправка…" }) as HTMLButtonElement;
    expect(btn.disabled).toBe(true);
    await user.click(btn);
    expect(FakeXHR.instances.length).toBe(1);

    act(() => xhr.respond(202, { job_id: JOB }));
    expect(await screen.findByRole("heading", { name: "Идёт анализ" })).toBeTruthy();
    expect(window.sessionStorage.getItem(JOB_STORAGE_KEY)).toBe(JOB);
    expect(await screen.findByText(/5 из 16 окон/)).toBeTruthy();
  });

  it.each([
    [401, {}, /Неверный ключ доступа/],
    [413, {}, /слишком большой/],
    [415, {}, /Формат файла не поддерживается/],
    [422, {}, /не приняты сервером/],
    [429, { "Retry-After": "30" }, /Повторите через 30 с/],
    [503, {}, /Сервис не готов/],
  ])("HTTP %i on upload shows a clear Russian message", async (status, headers, re) => {
    installFetch({ jobs: [processing()] });
    const user = userEvent.setup();
    render(<App config={CONFIG} />);
    const xhr = await submit(user);
    act(() => xhr.respond(status, { error: { code: "x", message: "x" } }, headers));
    expect((await screen.findByRole("alert")).textContent).toMatch(re);
    expect(screen.queryByTestId("verdict")).toBeNull();
  });

  it("rejects files above 256 MB on the client", async () => {
    installFetch({ jobs: [processing()] });
    const user = userEvent.setup();
    render(<App config={CONFIG} />);
    const big = movFile("big.mov", 10);
    Object.defineProperty(big, "size", { value: 256 * 1024 * 1024 + 1 });
    await user.type(screen.getByLabelText("Ключ доступа"), KEY);
    await user.upload(screen.getByTestId("file-input"), big);
    await user.click(screen.getByRole("button", { name: "Анализировать" }));
    expect((await screen.findByRole("alert")).textContent).toMatch(/256 МБ/);
    expect(FakeXHR.instances.length).toBe(0);
  });
});

describe("polling", () => {
  it("stops polling at done", async () => {
    const f = installFetch({
      jobs: [processing(), processing(), juryDone()],
      report: [json(processingReport), json(processingReport), json(violationReport)],
    });
    const user = userEvent.setup();
    render(<App config={CONFIG} />);
    await submitAccepted(user);
    expect(await screen.findByText("Обнаружено возможное нарушение")).toBeTruthy();
    const n = f.jobCalls();
    expect(n).toBe(3);
    await new Promise((r) => setTimeout(r, 80));
    expect(f.jobCalls()).toBe(n);
  });

  it("stops polling at failed, shows the reason and offers retry", async () => {
    const f = installFetch({
      jobs: [processing(), json({ job_id: JOB, status: "failed", error: failedReport.error })],
      report: [json(processingReport), json(failedReport)],
    });
    const user = userEvent.setup();
    render(<App config={CONFIG} />);
    await submitAccepted(user);
    const view = await screen.findByTestId("failed-view");
    expect(within(view).getByRole("heading").textContent).toMatch("Анализ не выполнен");
    expect(view.textContent).toMatch("Анализ не уложился в отведённое время");
    expect(screen.queryByText("Нарушение не обнаружено")).toBeNull();
    expect(view.className).toContain("card-failed");
    const n = f.jobCalls();
    await new Promise((r) => setTimeout(r, 80));
    expect(f.jobCalls()).toBe(n);

    await user.click(within(view).getByRole("button", { name: "Повторить анализ" }));
    await waitFor(() => expect(FakeXHR.instances.length).toBe(2));
  });

  it("treats transient 5xx/network errors as connection problems, never as a result", async () => {
    let phase: "errors" | "processing" | "done" = "errors";
    let i = 0;
    const errors = [
      () => json({ error: { code: "internal_error", message: "x" } }, 503),
      () => "network" as const,
      () => json({}, 502),
    ];
    installFetch({
      jobs: [
        () => {
          if (phase === "errors") return errors[i++ % errors.length]();
          return phase === "done" ? json(juryClean) : processing();
        },
      ],
      report: [() => (phase === "done" ? json(cleanReport) : json(processingReport))],
    });
    const user = userEvent.setup();
    render(<App config={CONFIG} />);
    await submitAccepted(user);
    await waitFor(() => expect(i).toBeGreaterThanOrEqual(4)); // every error kind seen, still retrying
    const alert = await screen.findByText("Это проблема соединения, а не результат анализа.");
    expect(alert.closest("[role=alert]")!.textContent).toMatch(/Проблема соединения/);
    expect(screen.queryByTestId("verdict")).toBeNull();
    expect(screen.queryByTestId("failed-view")).toBeNull();
    expect(screen.getByRole("heading", { name: "Идёт анализ" })).toBeTruthy();
    // Recovers once the server answers again.
    phase = "processing";
    await waitFor(() => expect(screen.queryByText(/Это проблема соединения/)).toBeNull());
    expect(screen.queryByTestId("verdict")).toBeNull();
    phase = "done";
    expect(await screen.findByText("Нарушение не обнаружено")).toBeTruthy();
    expect(screen.getByText("В пределах видимых действий и проверяемых правил")).toBeTruthy();
  });

  it("stops after the bounded number of failures and offers reconnect (still not a result)", async () => {
    let down = true;
    installFetch({ jobs: [() => (down ? ("network" as const) : json(juryClean))], report: [json(cleanReport)] });
    const user = userEvent.setup();
    render(<App config={{ ...CONFIG, maxConsecutiveFailures: 3 }} />);
    await submitAccepted(user);
    const btn = await screen.findByRole("button", { name: "Повторить подключение" });
    expect(screen.getByText(/Связь с сервером потеряна/)).toBeTruthy();
    expect(screen.queryByTestId("verdict")).toBeNull();
    down = false;
    await user.click(btn);
    expect(await screen.findByText("Нарушение не обнаружено")).toBeTruthy();
  });

  it("asks for the key again on 401 during polling and keeps the job id", async () => {
    installFetch({ jobs: [json({ error: { code: "unauthorized", message: "no" } }, 401)] });
    const user = userEvent.setup();
    render(<App config={CONFIG} />);
    await submitAccepted(user);
    expect((await screen.findByRole("alert")).textContent).toMatch(/Неверный ключ/);
    expect(screen.getByRole("region", { name: "Незавершённое задание" }).textContent).toContain(JOB);
  });

  it("recovers polling after refresh from a stored job id (key re-entered)", async () => {
    window.sessionStorage.setItem(JOB_STORAGE_KEY, JOB);
    installFetch({ jobs: [juryDone()], report: [json(violationReport)] });
    const user = userEvent.setup();
    render(<App config={CONFIG} />);
    await user.click(screen.getByRole("button", { name: "Продолжить" }));
    expect((await screen.findByRole("alert")).textContent).toMatch(/Введите ключ/);
    await user.type(screen.getByLabelText("Ключ доступа"), KEY);
    await user.click(screen.getByRole("button", { name: "Продолжить" }));
    expect(await screen.findByText("Обнаружено возможное нарушение")).toBeTruthy();
  });

  it("refuses to show a result when jury and report disagree", async () => {
    installFetch({ jobs: [json(juryClean)], report: [json(violationReport)] });
    const user = userEvent.setup();
    render(<App config={CONFIG} />);
    await submitAccepted(user);
    expect((await screen.findByTestId("failed-view")).textContent).toMatch(/несогласован/);
    expect(screen.queryByText("Нарушение не обнаружено")).toBeNull();
  });
});

describe("result", () => {
  it("renders two events; evidence uses authorized fetch and blob URLs revoked on reset", async () => {
    const f = installFetch({ jobs: [juryDone()], report: [json(violationReport)] });
    const user = userEvent.setup();
    render(<App config={CONFIG} />);
    await submitAccepted(user);
    expect(await screen.findByText("Обнаружено возможное нарушение")).toBeTruthy();
    expect(screen.getByText(/Найдено 2 события/)).toBeTruthy();
    const e1 = screen.getByTestId("event-e1");
    const e2 = screen.getByTestId("event-e2");
    expect(e1.textContent).toContain("Тестовый тип FIXTURE-A");
    expect(e1.textContent).toContain("00:12.345");
    expect(e1.textContent).toContain("12.345 с");
    expect(e2.textContent).toContain("00:47.000");
    for (const h of ["Что видно", "Какое требование может быть затронуто", "Источник", "Ограничения"]) {
      expect(within(e1).getByRole("heading", { name: h })).toBeTruthy();
    }
    const link = within(e1).getByRole("link", { name: "Официальный текст документа" });
    expect(link.getAttribute("rel")).toBe("noopener noreferrer");
    expect(link.getAttribute("href")).toBe("https://adilet.zan.kz/rus/docs/FIXTURE");

    const img = await within(e1).findByAltText("Кадр-доказательство в 00:12.345");
    expect(img.getAttribute("src")).toMatch(/^blob:/);
    await within(e2).findByAltText("Кадр-доказательство в 00:47.000");
    const evidenceCalls = f.calls.filter((c) => c.url.includes("/evidence/"));
    expect(evidenceCalls.map((c) => c.url).sort()).toEqual([
      `/api/v1/jobs/${JOB}/evidence/e1-0.jpg`,
      `/api/v1/jobs/${JOB}/evidence/e2-0.jpg`,
    ]);
    expect(evidenceCalls.every((c) => c.key === KEY)).toBe(true);

    // Enlarge in a dialog and close with Escape.
    await user.click(img.closest("button")!);
    const dialog = screen.getByRole("dialog", { name: "Кадр-доказательство в 00:12.345" });
    await user.keyboard("{Escape}");
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(dialog).toBeTruthy();

    const revoke = vi.mocked(URL.revokeObjectURL);
    revoke.mockClear();
    await user.click(screen.getByRole("button", { name: "Новый анализ" }));
    expect(revoke).toHaveBeenCalledTimes(2);
    expect(window.sessionStorage.getItem(JOB_STORAGE_KEY)).toBeNull();
  });

  it("marker and time-button clicks seek to t-1 s without changing the displayed timestamp", async () => {
    const f = installFetch({
      jobs: [juryDone()],
      report: [json(violationReport)],
      media: () => new Response(new Blob([new Uint8Array(8)], { type: "video/mp4" })),
    });
    const user = userEvent.setup();
    render(<App config={CONFIG} />);
    await submitAccepted(user);
    const video = (await screen.findByTestId("video")) as HTMLVideoElement;
    expect(f.calls.find((c) => c.url.endsWith("/media"))?.key).toBe(KEY);
    expect(video.getAttribute("src")).toMatch(/^blob:/);

    await user.click(screen.getByTestId("marker-e1"));
    expect(video.currentTime).toBeCloseTo(11.345, 3);
    await user.click(within(screen.getByTestId("event-e2")).getByRole("button", { name: /00:47\.000.*перейти в видео/ }));
    expect(video.currentTime).toBeCloseTo(46.0, 3);
    expect(screen.getByTestId("event-e1").textContent).toContain("00:12.345");

    // Unplayable media -> evidence-only fallback.
    fireEvent.error(video);
    expect(await screen.findByTestId("video-fallback")).toBeTruthy();
  });

  it("shows the test-mode banner only for mode=test", async () => {
    installFetch({ jobs: [juryDone()], report: [json(violationReport)] });
    const user = userEvent.setup();
    const { unmount } = render(<App config={CONFIG} />);
    await submitAccepted(user);
    await screen.findByTestId("verdict");
    expect(screen.getByText("ТЕСТОВЫЙ РЕЖИМ — результаты не являются реальным анализом")).toBeTruthy();
    unmount();

    installXHR();
    installFetch({ jobs: [json(juryClean)], report: [json({ ...cleanReport, mode: "production" })] });
    render(<App config={CONFIG} />);
    await submitAccepted(user);
    await screen.findByText("Нарушение не обнаружено");
    expect(screen.queryByText(/ТЕСТОВЫЙ РЕЖИМ/)).toBeNull();
  });
});

describe("secrets", () => {
  it("never writes the access key to localStorage, sessionStorage, cookies or URLs", async () => {
    const setItem = vi.spyOn(Storage.prototype, "setItem");
    const f = installFetch({
      jobs: [processing(), juryDone()],
      report: [json(processingReport), json(violationReport)],
    });
    const user = userEvent.setup();
    render(<App config={CONFIG} />);
    const xhr = await submitAccepted(user);
    await screen.findByText("Обнаружено возможное нарушение");
    await screen.findAllByAltText(/Кадр-доказательство/);

    for (const call of setItem.mock.calls) {
      expect(call.join("|")).not.toContain(KEY);
    }
    expect(setItem.mock.calls.map((c) => c[0])).toEqual([JOB_STORAGE_KEY]);
    expect(JSON.stringify({ ...window.localStorage })).not.toContain(KEY);
    expect(JSON.stringify({ ...window.sessionStorage })).not.toContain(KEY);
    expect(document.cookie).not.toContain(KEY);
    expect(window.location.href).not.toContain(KEY);
    expect(xhr.url).not.toContain(KEY);
    expect(f.calls.every((c) => !c.url.includes(KEY) && c.key === KEY)).toBe(true);
  });
});
