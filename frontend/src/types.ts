// Mirrors contracts/ui-report.schema.json and contracts/job-response.schema.json (frozen at G0).

export type Stage =
  | "receiving"
  | "probing"
  | "sampling"
  | "observing"
  | "verifying"
  | "rendering_evidence"
  | "committing"
  | "complete";

export type Verdict = "no_violation" | "violation";

export interface ErrorInfo {
  code: string;
  message: string;
}

export interface StageProgress {
  completed: number;
  total: number;
  unit: "windows" | "candidates" | "events" | "steps";
}

export interface UiEvidence {
  timestamp_sec: number;
  image_url: string;
}

export interface UiSource {
  rule_id: string;
  document_title: string;
  clause: string;
  official_url: string;
  local_excerpt: string;
}

export interface UiEvent {
  event_id: string;
  type: string;
  timecode_sec: number;
  description: string;
  evidence: UiEvidence[];
  source: UiSource;
  interval: { start_sec: number; end_sec: number };
  limitations_ru: string[];
}

export interface UiReport {
  job_id: string;
  status: "processing" | "done" | "failed";
  stage: Stage;
  elapsed_sec: number;
  progress?: StageProgress | null;
  mode?: "production" | "test";
  verdict?: Verdict | null;
  coverage_notice_ru?: string | null;
  events?: UiEvent[];
  media_available?: boolean;
  error?: ErrorInfo | null;
}

export interface JuryEvidenceFrame {
  timestamp_sec: number;
  image_base64: string;
}

export interface JuryViolation {
  type: string;
  timecode_sec: number;
  description: string;
  evidence_frames: JuryEvidenceFrame[];
}

export type JobResponse =
  | { job_id: string; status: "processing" }
  | { job_id: string; status: "failed"; error: ErrorInfo }
  | { job_id: string; status: "done"; verdict: Verdict; violations: JuryViolation[] };

export type JobDone = Extract<JobResponse, { status: "done" }>;
