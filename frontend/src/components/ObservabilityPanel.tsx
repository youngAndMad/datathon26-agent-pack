import { formatTimecode } from "../format";
import { COVERAGE_STATUS_RU, REVIEW_REQUIRED_BADGE } from "../messages";
import type { RuleCoverage, TimeInterval } from "../types";

interface Props {
  ruleCoverage: RuleCoverage[];
  qualityNotes: string[];
  reviewRequired: boolean;
  clean: boolean;
  onSeekInterval: (interval: TimeInterval) => void;
}

/**
 * Observability of the check itself (which rules could be checked, recording
 * quality). This is a property of the analysis, never a verdict.
 */
export function ObservabilityPanel({ ruleCoverage, qualityNotes, reviewRequired, clean, onSeekInterval }: Props) {
  if (!clean && ruleCoverage.length === 0 && qualityNotes.length === 0 && !reviewRequired) return null;
  const notCheckable = ruleCoverage.filter((r) => r.status !== "checked");
  return (
    <section className="card" aria-labelledby="obs-title" data-testid="observability">
      <h2 id="obs-title">Охват проверки</h2>
      {reviewRequired && (
        <p className="badge badge-review" data-testid="review-required">
          <span aria-hidden="true">ⓘ </span>
          {REVIEW_REQUIRED_BADGE}
          <span className="badge-sub"> — это свойство проверки, а не вывод о нарушении.</span>
        </p>
      )}
      {clean && notCheckable.length > 0 && (
        <p className="notice notice-warn" data-testid="clean-limits">
          Нарушение не обнаружено только в пределах проверенного. Полностью проверить по видео не удалось:{" "}
          {notCheckable.map((r) => r.type_ru).join("; ")}.
        </p>
      )}

      {clean && ruleCoverage.length === 0 && (
        <p className="hint">Сервер не передал сведения о том, какие правила удалось проверить.</p>
      )}
      {clean && ruleCoverage.length > 0 && notCheckable.length === 0 && (
        <p className="hint">Все проверяемые правила удалось проверить по видео.</p>
      )}

      {ruleCoverage.length > 0 && (
        <>
          <h3 className="sub-title">Проверенные правила</h3>
          <ul className="rule-list">
            {ruleCoverage.map((r) => {
              const st = COVERAGE_STATUS_RU[r.status] ?? { label: r.status, icon: "?" };
              const intervals = r.unverifiable_intervals ?? [];
              return (
                <li key={r.rule_id} className={`rule rule-${r.status}`} data-testid={`rule-${r.rule_id}`}>
                  <div className="rule-head">
                    <span className="rule-status">
                      <span aria-hidden="true" className="rule-icon">
                        {st.icon}
                      </span>{" "}
                      {st.label}
                    </span>
                    <span className="rule-type">{r.type_ru}</span>
                  </div>
                  {r.note_ru && <p className="hint">{r.note_ru}</p>}
                  {intervals.length > 0 && (
                    <p className="hint">
                      Не удалось проверить:{" "}
                      {intervals.map((iv, i) => (
                        <button
                          key={i}
                          type="button"
                          className="btn-link"
                          onClick={() => onSeekInterval(iv)}
                          aria-label={`Перейти к непроверяемому интервалу ${formatTimecode(iv.start_sec)} — ${formatTimecode(iv.end_sec)}`}
                        >
                          {formatTimecode(iv.start_sec)} — {formatTimecode(iv.end_sec)}
                        </button>
                      ))}
                    </p>
                  )}
                </li>
              );
            })}
          </ul>
        </>
      )}

      {qualityNotes.length > 0 && (
        <>
          <h3 className="sub-title">Качество записи</h3>
          <ul>
            {qualityNotes.map((n, i) => (
              <li key={i}>{n}</li>
            ))}
          </ul>
        </>
      )}
    </section>
  );
}
