import { formatBytes } from "../format";

export function UploadProgress({ loaded, total, onCancel }: { loaded: number; total: number; onCancel: () => void }) {
  const pct = total > 0 ? Math.min(100, Math.floor((loaded / total) * 100)) : 0;
  const finished = total > 0 && loaded >= total;
  return (
    <section className="card" aria-labelledby="uploading-title">
      <h2 id="uploading-title">Загрузка файла на сервер</h2>
      <label className="progress-label" htmlFor="upload-progress">
        {formatBytes(loaded)} из {formatBytes(total)} ({pct}%)
      </label>
      <progress id="upload-progress" max={total || 1} value={loaded} />
      {finished && <p className="hint">Файл передан, сервер сохраняет его…</p>}
      <button type="button" className="btn" onClick={onCancel}>
        Отменить загрузку
      </button>
    </section>
  );
}
