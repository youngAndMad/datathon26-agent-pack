import { afterEach, vi } from "vitest";
import { cleanup } from "@testing-library/react";

let n = 0;
// jsdom has no object URLs; provide deterministic stand-ins that tests can spy on.
URL.createObjectURL = vi.fn(() => `blob:test/${++n}`) as typeof URL.createObjectURL;
URL.revokeObjectURL = vi.fn() as typeof URL.revokeObjectURL;

afterEach(() => {
  cleanup();
  try {
    window.sessionStorage.clear();
    window.localStorage.clear();
  } catch {
    /* ignore */
  }
});
