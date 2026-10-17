# Requests from E (frontend)

None blocking. Frontend consumes the frozen contracts unchanged.

## R-E1 (non-blocking, A/B) Prefer an H.264 `preview.mp4` whenever possible
- Reason: the UI fetches `/api/v1/jobs/{id}/media` with the X-API-Key header into a Blob (D008); a native
  `<video>` cannot send the header, so Range requests are not used by the browser and the whole file is held
  in memory. If only `source.mov` (up to 256 MiB, possibly HEVC) is served, Chrome/Firefox often cannot play it
  and the UI falls back to evidence-only display (handled, but a worse reviewer experience).
- Change: keep producing a small no-audio H.264 preview with verified timestamp mapping (spec §5.4).
- Consumers: E (VideoPanel). No contract change.

## R-E2 (non-blocking, O) Integration wiring
- Serve `frontend/dist` as `settings.frontend_dist` (A already supports static SPA). Build: `cd frontend && npm ci && npm run build`.
- Fixture copies live in `frontend/src/mock/fixtures/`; a vitest test fails if they drift from `contracts/fixtures/`.
  If O changes contracts/fixtures, re-copy: `cp contracts/fixtures/* frontend/src/mock/fixtures/`.
