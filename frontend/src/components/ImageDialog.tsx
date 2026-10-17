import { useEffect, useRef } from "react";

export function ImageDialog({ src, alt, onClose }: { src: string; alt: string; onClose: () => void }) {
  const closeRef = useRef<HTMLButtonElement | null>(null);
  useEffect(() => {
    closeRef.current?.focus();
  }, []);
  return (
    <div
      className="dialog-backdrop"
      onClick={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <div
        className="dialog"
        role="dialog"
        aria-modal="true"
        aria-label={alt}
        onKeyDown={(e) => {
          if (e.key === "Escape") {
            e.stopPropagation();
            onClose();
          } else if (e.key === "Tab") {
            e.preventDefault();
            closeRef.current?.focus();
          }
        }}
      >
        <div className="dialog-head">
          <span>{alt}</span>
          <button ref={closeRef} type="button" className="btn" onClick={onClose}>
            Закрыть
          </button>
        </div>
        <img src={src} alt={alt} className="dialog-img" />
      </div>
    </div>
  );
}
