import { useId, useRef, useState, type DragEvent, type KeyboardEvent } from "react";
import { formatBytes } from "../format";

interface Props {
  apiKey: string;
  onApiKeyChange: (v: string) => void;
  file: File | null;
  onFileChange: (f: File | null) => void;
  disabled: boolean;
  error: string | null;
  pendingJobId: string | null;
  onSubmit: () => void;
  onResume: () => void;
  onDiscardPending: () => void;
}

function looksLikeMov(f: File): boolean {
  return /\.mov$/i.test(f.name) || f.type === "video/quicktime";
}

export function UploadForm(props: Props) {
  const { apiKey, onApiKeyChange, file, onFileChange, disabled, error, pendingJobId } = props;
  const inputRef = useRef<HTMLInputElement | null>(null);
  const [dragOver, setDragOver] = useState(false);
  const keyId = useId();
  const fileId = useId();
  const errId = useId();
  const hintId = useId();

  const openChooser = () => {
    if (!disabled) inputRef.current?.click();
  };
  const onDrop = (e: DragEvent<HTMLDivElement>) => {
    e.preventDefault();
    setDragOver(false);
    if (disabled) return;
    const f = e.dataTransfer.files?.[0];
    if (f) onFileChange(f);
  };
  const onZoneKey = (e: KeyboardEvent<HTMLDivElement>) => {
    if (e.key === "Enter" || e.key === " ") {
      e.preventDefault();
      openChooser();
    }
  };

  return (
    <section className="card" aria-labelledby="upload-title">
      <h2 id="upload-title">Загрузка видео</h2>

      {pendingJobId && (
        <div className="notice notice-info" role="region" aria-label="Незавершённое задание">
          <p>
            Найдено незавершённое задание <code>{pendingJobId}</code>. Ключ доступа не сохраняется — введите его
            ещё раз, чтобы продолжить отслеживание.
          </p>
          <div className="row">
            <button type="button" className="btn btn-primary" onClick={props.onResume} disabled={disabled}>
              Продолжить
            </button>
            <button type="button" className="btn" onClick={props.onDiscardPending} disabled={disabled}>
              Начать заново
            </button>
          </div>
        </div>
      )}

      <form
        onSubmit={(e) => {
          e.preventDefault();
          if (pendingJobId && !file) props.onResume();
          else props.onSubmit();
        }}
        noValidate
      >
        <div className="field">
          <label htmlFor={keyId}>Ключ доступа</label>
          <input
            id={keyId}
            type="password"
            autoComplete="off"
            spellCheck={false}
            value={apiKey}
            onChange={(e) => onApiKeyChange(e.target.value)}
            disabled={disabled}
            aria-describedby={`${keyId}-hint`}
          />
          <p id={`${keyId}-hint`} className="hint">
            Хранится только в памяти этой вкладки и не сохраняется.
          </p>
        </div>

        <div className="field">
          <span className="label" id={`${fileId}-label`}>
            Видеофайл
          </span>
          <div
            className={`dropzone${dragOver ? " dropzone-over" : ""}${disabled ? " dropzone-disabled" : ""}`}
            role="button"
            tabIndex={disabled ? -1 : 0}
            aria-disabled={disabled}
            aria-labelledby={`${fileId}-label ${fileId}-zone`}
            aria-describedby={hintId}
            onClick={openChooser}
            onKeyDown={onZoneKey}
            onDragOver={(e) => {
              e.preventDefault();
              if (!disabled) setDragOver(true);
            }}
            onDragLeave={() => setDragOver(false)}
            onDrop={onDrop}
            data-testid="dropzone"
          >
            <span id={`${fileId}-zone`}>
              {file ? (
                <>
                  Выбран файл: <strong>{file.name}</strong> ({formatBytes(file.size)}). Нажмите, чтобы выбрать другой.
                </>
              ) : (
                "Перетащите файл сюда или нажмите, чтобы выбрать"
              )}
            </span>
          </div>
          <input
            ref={inputRef}
            id={fileId}
            type="file"
            accept=".mov,video/quicktime"
            className="sr-only"
            tabIndex={-1}
            aria-hidden="true"
            data-testid="file-input"
            onChange={(e) => onFileChange(e.target.files?.[0] ?? null)}
            disabled={disabled}
          />
          <p id={hintId} className="hint">
            Формат: MOV. Максимальный размер: 256 МБ.
          </p>
          {file && !looksLikeMov(file) && (
            <p className="hint hint-warn">Файл не похож на MOV. Сервер проверит фактический формат.</p>
          )}
        </div>

        {error && (
          <p id={errId} className="notice notice-error" role="alert">
            <strong>Ошибка: </strong>
            {error}
          </p>
        )}

        <button type="submit" className="btn btn-primary btn-large" disabled={disabled || !file}>
          {disabled ? "Отправка…" : "Анализировать"}
        </button>
      </form>
    </section>
  );
}
