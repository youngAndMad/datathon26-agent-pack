// Russian copy. Error texts per code mirror cashwatch.dto.ERROR_MESSAGES_RU.

export const ERROR_MESSAGES_RU: Record<string, string> = {
  invalid_media: "Файл не является корректной видеозаписью",
  unsupported_container: "Формат контейнера не поддерживается (ожидается MOV)",
  limits_exceeded: "Видео превышает допустимые размеры или длительность",
  timeline_unsupported: "Не удалось однозначно определить временную шкалу видео",
  decode_failed: "Не удалось прочитать видеозапись",
  model_unavailable: "Модель анализа недоступна",
  model_output_invalid: "Модель вернула некорректный ответ",
  gpu_oom: "Недостаточно памяти ускорителя для анализа",
  storage_full: "Недостаточно места для хранения",
  worker_lost: "Обработчик задания был прерван",
  deadline_exceeded: "Анализ не уложился в отведённое время",
  evidence_invalid: "Не удалось подготовить достоверные кадры-доказательства",
  rule_registry_invalid: "Реестр правил недоступен или некорректен",
  input_unusable: "Видеозапись непригодна для анализа",
  internal_error: "Внутренняя ошибка обработки",
};

export const MAX_UPLOAD_BYTES = 256 * 1024 * 1024;

export const VERDICT_VIOLATION = "Обнаружено возможное нарушение";
export const VERDICT_CLEAN = "Нарушение не обнаружено";
export const CLEAN_QUALIFICATION = "В пределах видимых действий и проверяемых правил";
export const TEST_MODE_BANNER = "ТЕСТОВЫЙ РЕЖИМ — результаты не являются реальным анализом";
