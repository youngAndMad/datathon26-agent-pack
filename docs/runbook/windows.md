# Windows: локальная установка и запуск

Для отдельного экспериментального профиля 2B со скачиванием, проверкой файлов
и измерением памяти см. [windows-2b.md](windows-2b.md).

Быстрый локальный запуск с пересборкой интерфейса:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File deploy\start_local.ps1
```

Это явно тестовый режим с настоящей моделью на CPU, выгрузкой части весов на диск и синтетическим правилом TEST01. Скрипт задаёт OMP/MKL по 4 потока только дочерним процессам. Он не включает банковские правила и не гарантирует обработку за конкурсный лимит. Для повторного запуска сначала используйте `deploy\stop.ps1 -WindowsLocalTest`.

Для этого Windows-запуска очередь отключена процессной переменной `CASHWATCH_MAX_QUEUE_LENGTH=0`: измерения очереди на Mac не переносятся на CPU с дисковой выгрузкой. При повторном запуске готовность проверяется после истечения срока действия heartbeat предыдущего worker, чтобы не объявить модель готовой по устаревшей записи.

Параметр `-Precision float32` у `start_local.ps1` — только экспериментальный вариант. На i5-12450H (AVX2, 4 потока) синтетическая матричная операция с batch=128 была быстрее в FP32 (медианы 8.862 мс против 55.485 мс, n=5), но это **не** результат видеоанализа. FP32 загрузился и прогрелся, однако параллельное пользовательское задание завершилось `model_unavailable`; наш синтетический прогон затем прерван перезапуском и завершился `worker_lost`. Успешность/скорость всего анализа не установлены. Рабочий запуск возвращён к исходному BF16, лимиты инференса не увеличены.

Исправлено завершение модели при таймауте: supervisor теперь использует тот же проверяемый механизм завершения дерева, что и Windows stop-скрипт. Это устраняет путь, где завершался только venv launcher. Регрессионный тест `tests/inference/test_windows_supervisor.py` проверяет исчезновение настоящего дочернего интерпретатора и возможность следующего запуска; он использует scripted backend только внутри теста. Технический лог `generation_failure` содержит тип ошибки, этап и лимит времени, без кадров и текста модели.

Пересборка отдельно: `powershell -NoProfile -ExecutionPolicy Bypass -File deploy\build_ui.ps1`. Скрипт проверяет TypeScript и загружает временную JavaScript-копию исходного Vite-конфига через native loader; временный файл удаляется. Исходный конфиг, зависимости и версии не изменяются.

Последняя проверка 2026-10-17: обновлённый UI собран; удалена точно идентифицированная старая цепочка worker/model, оставшаяся после неполной остановки. Новый `/readyz` возвращал 200 с настоящей моделью. Синтетический MOV был принят, дошёл до `observing`, затем завершился `model_unavailable` через 127.078 секунды от загрузки (включая ожидание готовности worker). Успешный полный анализ на этом Windows-компьютере по-прежнему не подтверждён. Остановка теперь проверяет идентичность и приостанавливает всё дерево процессов до завершения, чтобы не оставлять работающие дочерние модели; при невозможности проверить дерево запись запуска сохраняется.

Сервис использует настоящий локальный Transformers/PyTorch backend. Production остаётся режимом по умолчанию и требует утверждённых правил. Для локальной разработки доступен явный тестовый режим с настоящей моделью и синтетическим правилом, как в `docs/LOCAL_SETUP.md`.

## Подготовка с доступом к интернету

Нужны Windows PowerShell 5.1+, Python 3.11 (Python launcher `py`), Node.js/npm и достаточно свободного диска для модели, Python-пакетов и видео. Команда выполняется из корня репозитория:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File deploy\setup.ps1 -DownloadModel -DownloadFfmpeg
```

Установка загружает локальный `uv==0.10.12` через имеющийся Python 3.11, устанавливает Python 3.12 и создаёт `.venv-windows`. Зависимости ставятся из `uv.lock` командой `uv sync --frozen --extra torch`; существующее `.venv` сохраняется. Затем собирается интерфейс, скачиваются модель и FFmpeg, создаётся случайный ключ в `secrets/jury_api_key`. Ключ не печатается, существующий ключ сохраняется. Его ACL ограничивается текущим пользователем и SYSTEM при создании. Для конкретного bootstrap Python используйте `-Python C:\path\python.exe`. Без ключей `-DownloadModel` и `-DownloadFfmpeg` соответствующие артефакты должны быть подготовлены заранее. `-SkipFrontend` пропускает сборку UI только при наличии готового `frontend/dist`.

Модель `Qwen/Qwen3-VL-4B-Instruct` скачивается штатным `scripts/model_fetch.py --only hf` с закреплённой в проекте ревизии. Используется общий `models/manifest.json`; после загрузки выполняется `scripts/model_verify.py --key qwen3-vl-4b-instruct` для проверки SHA-256. Манифест не доказывает качество модели или успешность инференса. Не запускайте скачивание одновременно с работающим сервисом.

FFmpeg берётся из Windows build provider Gyan. Скрипт сначала получает опубликованный SHA-256, затем проверяет архив до распаковки; адрес и фактический SHA сохраняются в `.tools/ffmpeg/download.json`. Это меняющийся release URL, а не заранее фиксированная версия: для повторяемой установки сохраняйте проверенный архив в `.tools/ffmpeg-stage-*`. Архив и лицензионные файлы сохраняются там же. Исполняемые файлы копируются в `.tools/ffmpeg/bin`, системный PATH не изменяется. Можно самостоятельно поставить FFmpeg и FFprobe в PATH и опустить `-DownloadFfmpeg`.

Скрипт останавливается при первой ошибке. Сетевой proxy, offline npm cache или запрет PyPI могут блокировать подготовку; выполненные шаги сохраняются, но успешная готовность не заявляется. Скрипты не меняют корпоративные proxy/pip/npm настройки.

## Запуск из локальных артефактов

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File deploy\start.ps1
```

Адрес: `http://127.0.0.1:8000`. По умолчанию CPU, production и Transformers. Для другого порта: `-Port 8010`. Для подходящего NVIDIA-устройства с совместимым драйвером и установленной CUDA-версией PyTorch: `-Device cuda`. Это явный выбор устройства, без автоматического перехода в облако. Скрипт не обновляет GPU-драйвер.

Пока банковские правила не утверждены, для локального запуска используйте:

```powershell
.\.venv-windows\Scripts\python.exe scripts\make_synthetic_media.py
powershell -NoProfile -ExecutionPolicy Bypass -File deploy\start.ps1 -Mode test
```

Этот режим загружает настоящую модель, но выбирает `rules/registry.test.yaml` с синтетическим правилом TEST01. Интерфейс и отчёты обозначают тестовый режим; выводы не подтверждают нарушения банковских правил. Откройте интерфейс, вставьте ключ из `secrets/jury_api_key` в своём терминале и загрузите `tests/fixtures/generated/marker_violation_40s.mov`. Синтетические ролики нужны для проверки работы приложения; успешный анализ на Windows пока не подтверждён.

Если нужно проверить только UI/API без весов модели, явно укажите `-Mode test -Backend fake`. Это имитация обработки без настоящего анализа. Скрипт запрещает сочетание production и fake. Веса модели обязательны для Transformers в любом режиме.

На машине с 4 ГБ VRAM модель 4B не считается подходящей для обычного CUDA-запуска. CPU может требовать значительной оперативной памяти и не уложиться в 5 минут; измерений реального видео на этой конфигурации ещё нет. Успешная установка не подтверждает прохождение конкурсного лимита. Если прогрев слишком долгий, запуск заканчивается ошибкой; `-TimeoutSeconds` меняет ожидание запуска, но не отменяет внутренние ограничения worker и дедлайн задания.

Перед запуском проверяются локальные артефакты, импорты модулей и реестр выбранного режима. Production требует утверждённый банковский реестр; тестовый реестр разрешён только с `-Mode test`. Установка зависимостей сама по себе не требует утверждения правил.

Worker и API запускаются скрыто; успех выводится только после HTTP 200 от `/readyz`. При неудаче созданные процессы останавливаются. Логи остаются в `data/logs/*.stdout.log` и `*.stderr.log`, записи PID и времени создания — в `data/run/*.windows.json`. Логи могут содержать локальную диагностику; не публикуйте их вместе с банковскими данными.

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File deploy\stop.ps1
```

Остановка проверяет время создания процесса, чтобы не завершить посторонний процесс с повторно использованным PID, затем завершает дерево worker/API. Незавершённые задания восстанавливаются штатными механизмами lease/deadline, а не превращаются в успешные результаты.

При запуске выставляются offline-флаги Hugging Face, отключается телеметрия и удаляются известные ключи внешних AI-провайдеров из окружения дочерних процессов. Это не является доказательством запрета всего исходящего трафика: проверку работы при блокированном egress нужно проводить отдельно. Публичный HTTPS и доступ жюри не создаются этим локальным запуском.

## Ограничения текущей проверки

### Проверенный запуск на Windows с небольшим объёмом свободной RAM

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File deploy\start.ps1 -Mode test -WindowsLocalTest -LowMemory -TimeoutSeconds 700
.\.venv-windows\Scripts\python.exe scripts\windows_local_check.py
# Остановка именно этого экземпляра:
powershell -NoProfile -ExecutionPolicy Bypass -File deploy\stop.ps1 -WindowsLocalTest
```

`-WindowsLocalTest` создаёт отдельный `data/windows-local` с наследованием прав рабочего каталога. Он разрешён только в тестовом режиме; существующие приватные каталоги и их ACL не изменяются. Логи и PID находятся в `data/windows-local/logs` и `data/windows-local/run`. Используйте этот вариант для синтетических данных, не для банковских записей.

`-LowMemory` явно включает CPU BF16 и размещение части весов на диске; бюджет 2 GiB относится к размещению весов, а не ко всей памяти процесса. В тестовом режиме ожидание прогрева worker увеличивается до 600 секунд. Ограничения обработки задания сохраняются. Общие конфиги и версии зависимостей не меняются.

Проверка 2026-10-17: UI вернул 200, запрос задания без ключа — 401, настоящая модель Qwen3-VL-4B прошла прогрев (`/readyz`: 200, `is_mock=false`). Однако синтетический секундный MOV с тестовым узором завершился `model_unavailable` через 63.765 секунды из-за лимита этапа инференса. После ошибки модель повторно прогревается, и readiness временно возвращает 503. Успешная обработка видео и соблюдение конкурсного лимита на этом компьютере **не подтверждены**. Дисковая выгрузка решает загрузку весов, но не обеспечивает нужную скорость CPU.

Проверка синтаксиса скриптов и Python не равнозначна запуску реального анализа. Для тестового запуска с настоящей моделью нужны загруженные веса и зависимости. Для production дополнительно требуется утверждение банковских правил командой. Тестовые и синтетические ответы не используются как доказательство работоспособности production.

Исходные конфигурации, pyproject.toml, uv.lock, существующая .venv и системные версии Python/Node/драйвера не меняются. Windows использует отдельное окружение и штатный `models/manifest.json`; переменные запуска действуют в процессе PowerShell и его дочерних процессах. Скрипты macOS остаются прежними.
# Local diagnostic confirmation (2026-10-17)

## Optional Windows resident INT8 profile

`powershell -NoProfile -ExecutionPolicy Bypass -File deploy/start_local.ps1 -CpuInt8`
selects an explicit CPU-only experimental path using the existing local Qwen
safetensors and installed PyTorch. It packs Linear layers individually into
dynamic INT8 (including a copy of the tied output head); embeddings keep BF16
storage and produce FP32 activations. Other parameters stay FP32. No complete
FP32 checkpoint copy, new download, runtime network, CUDA, or disk offload is
required. Packing happens at startup and does not modify the source weights.
Largest matrices are packed first to reduce transient memory. Quantization can
change predictions; accuracy on banking footage has not been established.
The INT8 profile allows up to 180 seconds for one observation call; the overall
270-second job deadline still bounds every call. Other profiles keep their
existing 60-second observation limit. Operational logs expose token counts and
elapsed seconds only, never token text or image contents.
macOS launch paths, shared configurations, Python and dependency versions are
unchanged. Without `-CpuInt8`, the earlier disk-offload launch remains available.

Synthetic tests cover tensor completeness, shape validation, tied output head,
BF16 embedding conversion, and actual tiny Qwen text/image generation. These
tests alone do not qualify the full model for the competition time budget.

Full-model results on this laptop (2026-10-17): initial packing in model order
crashed near the large output head; largest-first packing subsequently loaded
and warmed successfully. The synthetic one-second pattern MOV still failed at
the 60-second observation cap (64.422 seconds accepted-to-terminal). With the
explicit INT8 180-second observation cap it failed after 185.609 seconds.
Operational progress showed first token at 32.0 seconds, 96 tokens at 65.1,
and 384 tokens at 171.5 seconds. No complete observation/result was produced.
Thus this remains an experimental memory-saving extension, NOT a working fix
for this laptop's analysis deadline. A smaller model or faster inference runtime
requires a separate measured evaluation. Neither bank-video accuracy nor full
competition readiness is claimed. Summaries are local under
`data/windows-local/int8-smoke-summary.json` and
`data/windows-local/int8-180-smoke-summary.json`.

A synthetic `windows_pattern_smoke.mov` request against localhost returned HTTP
202, then failed after 64.813 seconds from acceptance. The operational worker
log recorded `generation_failure kind=timeout stage=observe call_limit_sec=60.0`.
The public error was `model_unavailable`. This confirms the current CPU/disk
profile does not finish even this observation within the configured call budget;
successful model loading/readiness is not successful video inference. No bank
footage was inspected for this check. The worker began reloading after the timeout.
