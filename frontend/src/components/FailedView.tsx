import type { RefObject } from "react";

interface Props {
  headingRef: RefObject<HTMLHeadingElement | null>;
  message: string;
  canRetrySameFile: boolean;
  onRetry: () => void;
  onChooseAnother: () => void;
}

export function FailedView({ headingRef, message, canRetrySameFile, onRetry, onChooseAnother }: Props) {
  return (
    <section className="card card-failed" aria-labelledby="failed-title" data-testid="failed-view">
      <h2 id="failed-title" ref={headingRef} tabIndex={-1}>
        <span aria-hidden="true">✕ </span>Анализ не выполнен
      </h2>
      <p className="failed-reason">
        <strong>Причина: </strong>
        {message}
      </p>
      <p className="muted">Результат проверки отсутствует. Это не означает, что нарушений нет.</p>
      <div className="row">
        {canRetrySameFile && (
          <button type="button" className="btn btn-primary" onClick={onRetry}>
            Повторить анализ
          </button>
        )}
        <button type="button" className="btn" onClick={onChooseAnother}>
          Выбрать другой файл
        </button>
      </div>
    </section>
  );
}
