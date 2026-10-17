import { useRef, useState } from "react";
import { useAuthorizedObjectUrl } from "../blobs";
import { formatSeconds, formatTimecode } from "../format";
import type { UiEvidence } from "../types";
import { ImageDialog } from "./ImageDialog";

export function evidenceAlt(timestampSec: number): string {
  return `Кадр-доказательство в ${formatTimecode(timestampSec)}`;
}

export function EvidenceThumb({ evidence }: { evidence: UiEvidence }) {
  const state = useAuthorizedObjectUrl(evidence.image_url);
  const [open, setOpen] = useState(false);
  const triggerRef = useRef<HTMLButtonElement | null>(null);
  const alt = evidenceAlt(evidence.timestamp_sec);

  return (
    <figure className="thumb">
      {state.status === "ok" ? (
        <button
          ref={triggerRef}
          type="button"
          className="thumb-btn"
          onClick={() => setOpen(true)}
          aria-haspopup="dialog"
          title="Увеличить кадр"
        >
          <img src={state.url} alt={alt} loading="lazy" />
        </button>
      ) : (
        <div className="thumb-placeholder" role="img" aria-label={alt}>
          {state.status === "loading" ? "Загрузка кадра…" : "Кадр недоступен"}
        </div>
      )}
      <figcaption>
        {formatTimecode(evidence.timestamp_sec)} ({formatSeconds(evidence.timestamp_sec)})
      </figcaption>
      {open && state.status === "ok" && (
        <ImageDialog
          src={state.url}
          alt={alt}
          onClose={() => {
            setOpen(false);
            triggerRef.current?.focus();
          }}
        />
      )}
    </figure>
  );
}
