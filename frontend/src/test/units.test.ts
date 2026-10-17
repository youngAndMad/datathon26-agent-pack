import { describe, expect, it } from "vitest";
import { readFileSync, readdirSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { formatTimecode, formatSeconds, seekTarget, pluralEvents } from "../format";
import { ApiError, isApiPath, messageForStatus, parseRetryAfter } from "../api";
import { stepIndex } from "../stages";

describe("format", () => {
  it("formats mm:ss.mmm", () => {
    expect(formatTimecode(12.345)).toBe("00:12.345");
    expect(formatTimecode(47)).toBe("00:47.000");
    expect(formatTimecode(125.5)).toBe("02:05.500");
    expect(formatTimecode(0)).toBe("00:00.000");
    expect(formatSeconds(12.345)).toBe("12.345 с");
  });
  it("seeks 1 s before, clamped", () => {
    expect(seekTarget(12.345)).toBeCloseTo(11.345);
    expect(seekTarget(0.4)).toBe(0);
    expect(seekTarget(50, 30)).toBe(30);
  });
  it("russian plural", () => {
    expect(pluralEvents(1)).toBe("событие");
    expect(pluralEvents(2)).toBe("события");
    expect(pluralEvents(5)).toBe("событий");
    expect(pluralEvents(11)).toBe("событий");
    expect(pluralEvents(21)).toBe("событие");
  });
});

describe("api helpers", () => {
  it("parses Retry-After seconds and HTTP dates", () => {
    expect(parseRetryAfter("30")).toBe(30);
    expect(parseRetryAfter(new Date(1_000_000 + 10_000).toUTCString(), 1_000_000)).toBe(10);
    expect(parseRetryAfter(null)).toBeUndefined();
  });
  it("only same-origin API paths may carry the key", () => {
    expect(isApiPath("/api/v1/jobs/abc/evidence/e1-0.jpg")).toBe(true);
    expect(isApiPath("https://evil.example/api/v1/x")).toBe(false);
    expect(isApiPath("//evil.example/api/v1/x")).toBe(false);
    expect(isApiPath("/api/v1/jobs/../../etc")).toBe(false);
  });
  it("classifies transient errors", () => {
    expect(new ApiError("http", "", { status: 503 }).transient).toBe(true);
    expect(new ApiError("http", "", { status: 429 }).transient).toBe(true);
    expect(new ApiError("network", "").transient).toBe(true);
    expect(new ApiError("http", "", { status: 404 }).transient).toBe(false);
    expect(new ApiError("http", "", { status: 401 }).unauthorized).toBe(true);
    expect(messageForStatus(422, "decode_failed")).toContain("Не удалось прочитать видеозапись");
  });
  it("maps stages to the five UI steps", () => {
    expect(stepIndex("receiving")).toBe(0);
    expect(stepIndex("probing")).toBe(1);
    expect(stepIndex("sampling")).toBe(1);
    expect(stepIndex("observing")).toBe(2);
    expect(stepIndex("verifying")).toBe(3);
    expect(stepIndex("rendering_evidence")).toBe(4);
    expect(stepIndex("committing")).toBe(4);
    expect(stepIndex("complete")).toBe(5);
  });
});

describe("fixture copies", () => {
  it("frontend fixture copies are byte-identical to contracts/fixtures", () => {
    const here = (p: string) => fileURLToPath(new URL(p, import.meta.url));
    const contracts = here("../../../contracts/fixtures/");
    const local = here("../mock/fixtures/");
    const names = readdirSync(contracts).sort();
    expect(readdirSync(local).sort()).toEqual(names);
    for (const n of names) {
      expect(readFileSync(local + n).equals(readFileSync(contracts + n)), n).toBe(true);
    }
  });
});
