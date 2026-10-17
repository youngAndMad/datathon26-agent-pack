# Agent E — Reviewer interface

Read AGENTS.md and implementation sections 5 and 11. Own frontend/ and frontend tests. Root dependency decisions remain with the integrator; frontend package files can be owned here if explicitly agreed at G0.

Build a locally bundled React/TypeScript/Vite interface in Russian. Use frozen jury and UI-report contracts and explicit synthetic fixtures while backend work proceeds. Deliver upload, key entry, upload/processing stages, polling, exact two verdicts, failure state, multiple event cards, source frames, rule excerpts and timeline seeking.

Store the entered key only in memory. Do not bundle the jury key or put it into URLs/localStorage. Use same-origin authorized fetch; coordinate blob URLs or short-lived HttpOnly sessions for video playback. Revoke object URLs when no longer needed. Do not assume MOV/HEVC plays in every browser; handle an authorized preview or evidence-only fallback.

Display source facts and possible-rule interpretation separately. Never imply final guilt or use an arbitrary confidence percentage. Expose relevant visibility limits without replacing the required verdict. Clicking an event seeks correctly. Polling stops on done/failed; temporary HTTP issues are not a clean result.

Bundle fonts/assets locally; no analytics/CDN/remote logging. Cover keyboard access, readable loading/error states and mobile layout. Show actual progress, not random animation percentages. Handoff includes build output, end-to-end tests against fixtures, then against the real API when integrated. Do not claim detection tests from fixture screenshots.
