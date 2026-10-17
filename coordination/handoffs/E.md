# Handoff — Agent E (reviewer interface)

Status: ready_for_integration (not a release-gate claim). Synthetic fixtures only; no real media touched.
Not verified against the real API yet (fixture/mocked tests only).

## Owned files changed (all new, no worktree/commit)
- `frontend/package.json`, `frontend/package-lock.json`, `frontend/.gitignore`, `frontend/index.html`,
  `frontend/vite.config.ts`, `frontend/tsconfig.json`
- `frontend/src/{main.tsx,App.tsx,api.ts,blobs.ts,types.ts,messages.ts,format.ts,stages.ts,styles.css}`
- `frontend/src/components/{UploadForm,UploadProgress,ProcessingView,ResultView,EventCard,EvidenceThumb,ImageDialog,Timeline,FailedView}.tsx`
- `frontend/src/mock/{install.ts,noop.ts}`, `frontend/src/mock/fixtures/*` (byte copies of `contracts/fixtures/*`)
- `frontend/src/test/{setup.ts,helpers.ts,app.test.tsx,units.test.ts}`
- `coordination/requests/E.md`, `coordination/handoffs/E.md`

Pinned exact versions (no ^/~): react/react-dom 19.3.1, typescript 7.0.2, vite 8.3.4, @vitejs/plugin-react 6.1.2,
vitest 5.0.5, @testing-library/react 16.3.3, @testing-library/dom 10.4.2, @testing-library/user-event 14.6.8,
jsdom 29.1.1 (30.x requires Node >= 24.15; host has 24.8), @types/react 19.3.0, @types/react-dom 19.3.0, @types/node 24.19.2.

## Functionality
- Russian single responsive screen: idle (key input type=password, keyboard-operable drop zone + chooser, MOV / 256 МБ
  hint, client-side 256 MiB check, "Анализировать"), uploading (XHR real bytes progress, cancel; submit disabled →
  no duplicate POST), processing (5 stages Загрузка / Подготовка видео / Анализ действий / Проверка событий /
  Подготовка результата with text state labels, elapsed time from report.elapsed_sec, progress bar ONLY from
  report.progress completed/total), done, failed.
- Polling: GET /api/v1/jobs/{id} (authoritative) + GET …/report every 1.5 s; transient (network, 5xx, 429, malformed)
  → visible "Проблема соединения … (не результат анализа)", exponential backoff capped 15 s (honours Retry-After),
  after 40 consecutive failures stop and offer "Повторить подключение". 401 → back to key entry, job kept. 404/410 →
  failed-style message. Stops at done/failed. Result shown only if jury verdict, violations count and report agree;
  otherwise a failed-style "Ответ сервера несогласован".
- Done: "Обнаружено возможное нарушение" + "Найдено N событий", or "Нарушение не обнаружено" + "В пределах видимых
  действий и проверяемых правил"; coverage notice; video via authorized fetch → blob URL (unplayable → evidence-only
  fallback + download link); timeline markers; marker/time button seek to max(0, t−1) clamped to duration, stored
  timestamp text unchanged; event cards (type, mm:ss.mmm + seconds, Что видно / Какое требование может быть
  затронуто / Источник with excerpt + official link rel="noopener noreferrer" (http/https only) / Ограничения);
  1–3 evidence thumbnails via authorized fetch → blob URL, enlarge dialog (Esc, focus return), alt
  "Кадр-доказательство в {time}". All object URLs revoked on reset/unmount (BlobRegistry). mode=test → red banner
  "ТЕСТОВЫЙ РЕЖИМ — результаты не являются реальным анализом".
- Failed: "Анализ не выполнен", reason = error.message (fallback ERROR_MESSAGES_RU[code]), "это не означает, что
  нарушений нет", "Повторить анализ" (same file) / "Выбрать другой файл". Red styling, never success styling.
- Key: React state only. Only `cashwatch.job_id` is written to sessionStorage (refresh recovery asks the key again).
  Key is sent only as X-API-Key to same-origin paths matching `^/api/v1/…` (evidence image_url outside that pattern is refused).
- No confidence values, no guilt language, no CDN/fonts/analytics; system font stack; dark-mode aware; mobile layout.

## Contracts consumed
ui-report.schema.json, job-response.schema.json, jury-result.schema.json, openapi.json (analyze status codes),
contracts/fixtures/*, dto.ERROR_MESSAGES_RU (copied into src/messages.ts), D007 (3 decimals), D008 (evidence/media).
Checked against A's handoff: CSP `img-src 'self' blob:; media-src 'self' blob:` is compatible.

## Verification (Apple M4 Pro, macOS 26.6, Node 24.8.0, npm 11.6.0)
```
cd frontend && npm ci && npm test -- --run && npm run build
```
- `npm ci`: 144 packages, no engine warnings.
- `npm test -- --run`: 2 files, 27 tests passed (~2.4 s). Covers: upload with mocked XHR (header, multipart `file`,
  byte progress, duplicate submit blocked, job id in sessionStorage); HTTP 401/413/415/422/429(Retry-After)/503
  messages; client size limit; polling stops at done and at failed; failed message + retry re-uploads; transient
  503/network/502 shown as connection problem, never a verdict, then recovers; bounded-failure stop + reconnect;
  401 during polling; refresh recovery; jury/report mismatch refused; two events render, evidence fetched with
  X-API-Key, blob URLs revoked on reset; marker → currentTime 11.345, time button → 46.000, displayed 00:12.345
  unchanged; video error → fallback; test-mode banner shown/hidden; key never in setItem calls/local/session
  storage/cookie/URL; format/Retry-After/path-guard/stage-mapping units; fixture copies byte-identical to contracts.
- `npm run build` (tsc --noEmit + vite build): dist/index.html 0.48 kB, assets/index-*.js 250.1 kB (78.4 kB gzip),
  assets/index-*.css 6.1 kB (2.0 kB gzip). No source maps.
- Dist grep proof:
  - `grep -c -iE "FIXTURE|ФИКСТУР|mock|e1-0\.jpg|00000000-0000-4000|ТЕСТОВЫЕ ДАННЫЕ|MockXHR" dist/assets/*.js dist/index.html`
    → 0 / 0 (same grep on a `--mode fixtures` build → match, so the grep is meaningful).
  - `grep -ohE "https?://[A-Za-z0-9./_-]+" -r dist | sort -u` → only w3.org XML/SVG/MathML namespace constants and
    `https://react.dev/errors/` (React's minified-error text prefix); no request is made to any of them. Official
    legal links come only from report data at runtime.
  - Storage APIs in dist: only `sessionStorage` (job id).
- Manual smoke: `npx vite --mode fixtures` in Chrome with a synthetic in-page 4 KB File: ФИКСТУРЫ banner, stages +
  "4 из 16 окон", violation with 2 events, both evidence JPEGs decoded via blob URLs, unplayable-video fallback,
  test-mode banner, sessionStorage only has job id, no console errors (n=1).

## How to run
- Fixtures (dev only): `cd frontend && npm run dev:fixtures` → http://127.0.0.1:5173. Visible "ФИКСТУРЫ" banner.
  Mock is selected by Vite alias `@mock-layer` → `src/mock/install.ts` only when mode=fixtures, else `noop.ts`.
  Scenarios by file name: `clean`, `fail`, `busy` (429), `flaky` (503s during polling), otherwise violation; key `bad` → 401.
- Against local API: `npm run dev` (proxy /api → http://127.0.0.1:8000). Production: `npm run build`, served
  same-origin by the API from `frontend/dist`.

## Latency/memory
Not measured beyond build/test time (UI only). Note: the media preview is fetched fully into a Blob in browser memory
(up to the file size; source.mov fallback can be up to 256 MiB).

## Limitations / assumptions
- Not yet run against the real API/worker (fixture and mocked-fetch tests only). Detection quality is NOT shown by
  any of these tests.
- MOV/HEVC playback depends on the browser; fallback is evidence-only + download link.
- Without a known video duration, timeline marker positions are approximate (labeled as such).
- Elapsed time base is server elapsed_sec when a report exists, else local time since submit.
- Fixture copies must be refreshed if contracts/fixtures change (test enforces).

## Requested integration action
- O: wire `frontend/dist` into A's static SPA setting; add `cd frontend && npm ci && npm test -- --run && npm run build`
  to the release check; commit frontend/ (excluding node_modules/, dist/ per frontend/.gitignore).
- F: run an end-to-end smoke against the real API with a synthetic MOV and record it.
- See coordination/requests/E.md (non-blocking).
