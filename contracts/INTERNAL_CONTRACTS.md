# Internal interfaces to freeze at G0

All paths are local and trusted only after containment validation. All times are seconds relative to the original presentation timeline. Numbers must be finite. IDs and enumerations are stable; unknown is explicit rather than null-by-accident. The lead agent generates typed DTOs from this contract and records any refinement before dependent agents begin.

## JobInput and Deadline

`JobInput`: job_id:string; principal_id:string; source_path:Path; source_sha256:string; request_started_at:UTC timestamp; accepted_at:UTC timestamp; deadline_at:UTC timestamp; attempt:int; lease_generation:int; config_version:string.

`Deadline`: original persistent expiry plus monotonic remaining budget; remaining_sec(), require_budget(stage, estimated_sec), expired(). Never reset at retries. After a process restart, reconstruct a conservative remaining budget from persisted UTC time.

## Frame and media records

`FrameRef`: frame_id:string; source_sha256:string; ordinal:int>=0; pts:int; time_base_num:int; time_base_den:int>0; relative_sec:float>=0; local_path:Path; sha256:string; width:int; height:int; transforms:list of {kind, parameters}. Preserve original rational values and one deterministic timestamp serialization precision.

`VideoMetadata`: source_sha256, size_bytes, container, codec, dimensions, display_rotation, duration_sec, playable_origin metadata, decode_warnings. An ambiguous timeline is a typed failure.

`SamplingPlan`: profile_id; windows:list of {window_id, start_sec, end_sec, requested_sample_times}; visual_token_limit; maximum_frames_per_call; mandatory_window_ids. Explicitly cover the first and last playable frames/intervals. Extra proposal windows do not replace mandatory windows.

`FrameManifest`: VideoMetadata; frame_refs; window-to-frame mapping; observed coverage; decoding statistics. Each frame retains its actual time rather than the requested sample time.

## Perception records

`ObservationRequest`: job_id; window_id; frame_refs; task_version; allowed_predicate_ids; max_output_tokens. Model adapter resolves image bytes locally. Labels from organizers never enter this request during evaluation.

`ObservationBatch`: window_id; observations:list; parse_status; visibility limitations; elapsed_ms; model_version. Each observation has observation_id, frame_ids, actor_role hypotheses, anonymous per-clip track references, object hypotheses, action/relation and a bounded neutral statement. No source citation or free-form legal verdict.

`PredicateAssessment`: predicate_id; state in supported/contradicted/unknown; supporting_frame_ids; contradicting_frame_ids; interval_start_sec; interval_end_sec; visibility; reasoning_note bounded to visible facts. Absence requires explicitly observed opportunity boundaries.

`VerificationRequest`: candidate_id; approved rule predicate definitions and counterexamples; local frame refs; known interval boundaries; generation budget. `VerificationResult`: assessments; alternatives; interval_complete:boolean; visibility_sufficient:boolean; schema_valid:boolean.

## Rules and reporting

`CandidateEvent`: candidate_id, rule_id/version, start/end, anchor_frame_id, predicate assessments, counterexamples considered, provenance observation IDs, review state in proposed/accepted/rejected/unresolved. An unresolved candidate never becomes a public violation automatically.

`AcceptedEvent`: stable event_id, approved rule_id/version, Russian type, first decisive anchor reference, 1–3 evidence refs including anchor, bounded factual text, explanation template ID. All required predicate gates must pass.

`AnalysisReport`: job_id; status; source metadata/hash; run versions; timings; sampling coverage; completed_required_stages; visual limitations; accepted events; unresolved review notes; exact jury_result. UI response must project out private paths, raw prompts and model internals.

`Coverage`: profile_id, required_window_ids, completed_window_ids, failed_window_ids, visual_limitations. completed means the declared tested plan ran, not that every source frame was inspected. Technical incompleteness is not ordinary visual uncertainty.

`EvidenceFrame`: FrameRef plus JPEG bytes/local JPEG path, encoding transform, image SHA-256. Base64 is created only at the external projection boundary.

## UI report response to freeze

Top-level: job_id; status; stage; elapsed_sec; optional progress {completed,total,unit}; if done, verdict, coverage_notice_ru, events. If failed, safe error code/message.

Each event: event_id; type; timecode_sec; description; evidence:[{timestamp_sec, image_url or image_base64}]; source:{rule_id, document_title, clause, official_url, local_excerpt}; interval:{start_sec,end_sec}; limitations_ru. The UI and backend must agree on evidence transport once at G0; never add these fields to the strict jury result.

## Stage and error vocabulary

Stages: receiving, probing, sampling, observing, verifying, rendering_evidence, committing, complete.

Errors: invalid_media, unsupported_container, limits_exceeded, timeline_unsupported, decode_failed, model_unavailable, model_output_invalid, gpu_oom, storage_full, worker_lost, deadline_exceeded, evidence_invalid, rule_registry_invalid, input_unusable, internal_error. Internal errors have safe messages; detailed diagnostics stay local without media/scene text or secrets.

## Storage boundary

`reserve_capacity(principal, expiry)`, `persist_upload(...)`, `enqueue_job(...)`, `claim_job(worker_id)`, `heartbeat(job_id,generation)`, `commit_result(job_id,generation,validated_result_path)`, `fail_job(job_id,generation,error)`, `recover_expired_jobs(now)`.

Claim/commit/fail use short transactions and fencing generations. The API never invokes the model. The worker cannot publish without the validator. A sweeper resolves expired running jobs after restarts using the original deadline and bounded attempt count.
