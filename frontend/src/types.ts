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
  rule_coverage?: RuleCoverage[];
  quality_notes_ru?: string[];
  review_required?: boolean;
  reviews?: EventReview[];
  error?: ErrorInfo | null;
}

// ---- g0-1.2.0: observability, reviewer decisions, history, export

export interface TimeInterval {
  start_sec: number;
  end_sec: number;
}

export type RuleCoverageStatus = "checked" | "partially_checked" | "not_checkable";

export interface RuleCoverage {
  rule_id: string;
  type_ru: string;
  status: RuleCoverageStatus;
  unverifiable_intervals?: TimeInterval[];
  note_ru?: string;
}

export type ReviewDecision = "confirmed" | "rejected" | "needs_review" | "missed";

export interface ReviewRequest {
  decision: ReviewDecision;
  comment: string;
  corrected_type?: string | null;
  corrected_timecode_sec?: number | null;
  reviewer: string;
}

export interface EventReview {
  review_id: string;
  event_id: string;
  decision: ReviewDecision;
  comment?: string;
  corrected_type?: string | null;
  corrected_timecode_sec?: number | null;
  reviewer?: string;
  reviewed_at: string;
}

export interface JobSummary {
  job_id: string;
  status: "processing" | "done" | "failed";
  received_at: string;
  finished_at?: string | null;
  verdict?: Verdict | null;
  event_count?: number;
  reviewed_event_count?: number;
  review_required?: boolean;
  mode?: "production" | "test";
  error_code?: string | null;
}

export interface JobListResponse {
  jobs: JobSummary[];
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
