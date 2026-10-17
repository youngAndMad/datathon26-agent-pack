import type { Stage, StageProgress } from "./types";

export interface StageStep {
  label: string;
  stages: Stage[];
}

export const STAGE_STEPS: StageStep[] = [
  { label: "Загрузка", stages: ["receiving"] },
  { label: "Подготовка видео", stages: ["probing", "sampling"] },
  { label: "Анализ действий", stages: ["observing"] },
  { label: "Проверка событий", stages: ["verifying"] },
  { label: "Подготовка результата", stages: ["rendering_evidence", "committing"] },
];

/** Index of the step currently running; STAGE_STEPS.length when complete. */
export function stepIndex(stage: Stage): number {
  if (stage === "complete") return STAGE_STEPS.length;
  const i = STAGE_STEPS.findIndex((s) => s.stages.includes(stage));
  return i < 0 ? 0 : i;
}

const UNIT_RU: Record<StageProgress["unit"], string> = {
  windows: "окон",
  candidates: "кандидатов",
  events: "событий",
  steps: "шагов",
};

export function progressText(p: StageProgress): string {
  return `${p.completed} из ${p.total} ${UNIT_RU[p.unit] ?? ""}`.trim();
}
