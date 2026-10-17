"""Opt-in desk detail views, bounded by the existing verification image budget."""
from __future__ import annotations

import math
from cashwatch.errors import PipelineError


def desk_roi_config(sampling: dict, rule_id: str) -> dict | None:
    cfg = sampling.get('desk_roi')
    if not cfg or not cfg.get('enabled', False):
        return None
    if rule_id not in cfg.get('rule_ids', ['CW02', 'CW11', 'CW14']):
        return None
    bounds = cfg.get('bounds')
    if (not isinstance(bounds, list) or len(bounds) != 4 or
        any(type(x) not in (int, float) or not math.isfinite(x) for x in bounds) or
        not (0 <= bounds[0] < bounds[2] <= 1 and 0 <= bounds[1] < bounds[3] <= 1)):
        raise PipelineError('internal_error', 'desk ROI needs normalized x0,y0,x1,y1 bounds')
    return cfg


def add_desk_crops(media, source, meta, work_dir, context_frames, candidate,
                   cfg, slots, long_side_px, deadline):
    """Decode native views by time, require matching original PTS, then crop.

    The caller reserves crop slots first; full views stay in the request. No ROI
    coordinates are inferred from competition footage or reused across cameras.
    """
    if slots <= 0 or not context_frames:
        return context_frames
    from cashwatch.media.roi import crop_frame
    ordered = sorted(context_frames, key=lambda f: (f.frame_id != candidate.anchor_frame_id,
                                                   f.relative_sec, f.frame_id))
    selected = ordered[:slots]
    native = media.frames_for_times(source, meta, [f.relative_sec for f in selected], work_dir,
                                    long_side_px=max(meta.width, meta.height), deadline=deadline)
    def key(f):
        return f.source_sha256, f.pts, f.time_base_num, f.time_base_den
    by_pts = {key(f): f for f in native}
    crops = []
    for original in selected:
        exact = by_pts.get(key(original))
        if exact is None:
            raise PipelineError('evidence_invalid', 'native ROI extraction changed source PTS')
        try:
            crops.append(crop_frame(exact, media.resolve_frame(exact, work_dir), work_dir,
                                    cfg['bounds'], long_side_px))
        except (ValueError, OSError) as exc:
            raise PipelineError('evidence_invalid', 'native ROI validation failed') from exc
    return sorted(context_frames + crops, key=lambda f: (f.relative_sec, f.frame_id))


def crop_context_note(frames) -> str | None:
    indices = [str(i) for i, f in enumerate(frames, 1) if any(t.kind == 'crop' for t in f.transforms)]
    if not indices:
        return None
    return ('Frames ' + ', '.join(indices) + ' are desk close-ups of the same source times, '
            'not new actions. Use the full views for people and sequence context. '
            'An item or person outside a crop is not evidence of absence.')
