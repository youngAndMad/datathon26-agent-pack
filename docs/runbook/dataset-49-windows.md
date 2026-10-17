# Локальная оценка всего набора на Windows / CUDA

Команды выполняет пользователь на своём ноутбуке. Видео, Excel, разметку и результаты отдельных роликов не передавать облачным агентам. Для обсуждения качества достаточно агрегатов без имён файлов и описаний сцен.

Текущий `start_windows_cuda.ps1` запускает TEST01: это проверка работоспособности, а не банковского качества. Для качества используйте отдельный `evaluate_windows_cuda.ps1`, который вызывает реальный пайплайн с банковскими правилами из `rules/registry.yaml`. Это development-оценка; она не меняет статус production-правил и не проверяет HTTP-загрузку. HTTP-проверка синтетического ролика выполняется отдельно через `start_windows_cuda.ps1 -Smoke`.

## 1. Подготовить проверенные метки всех видео

В Excel подготовьте CSV UTF-8 с колонками:

```csv
file,label_verdict,label_timestamps_sec,group,split
synthetic-positive.mov,violation,12.5,scene-a,dev
synthetic-negative.mov,no_violation,,scene-b,dev
```

Это только пример формата. Реальные строки берутся из таблицы организаторов, а не из ответов модели. `file` — путь относительно папки видео; `label_verdict` — строго `violation` или `no_violation`; таймкоды — секунды через `;`. Для проверки всего знакомого набора используйте `split=dev`. Связанные дубли одной сцены получают один `group`. Типы нарушений не придумывайте: без проверенных типов нельзя оценить качество классификации типов.

Сохраните файл как `evaluation/manifest/private/labels-49.csv`. В Excel выбирайте CSV UTF-8 с запятыми; иной разделитель нужно привести к формату выше.

В репозитории также есть локальный конвертер XLSX `scripts/local_labels_from_xlsx.py`. Сначала выполните его с `--xlsx <путь> --show-header`, затем задайте точные `--file-col`, `--label-col`, `--time-col` и `--out evaluation/manifest/private/labels-49.csv`. Проверьте преобразованные метки вручную; заполните оставленные пустыми `group` и `split`. Для числового Excel-времени используется `--time-unit excel-day`, для секунд — `sec`.

## 2. Построить манифест

Из PowerShell в корне репозитория:

```powershell
cd C:\Users\berik\Desktop\project
$env:PATH = "$PWD\.tools\ffmpeg\bin;$env:PATH"
.\.venv-windows\Scripts\python.exe evaluation\manifest\build_manifest.py --clips-dir "C:\Users\berik\Downloads\open" --labels evaluation\manifest\private\labels-49.csv --out evaluation\manifest\private\manifest-49.json
```

Путь `--clips-dir` должен указывать на фактическую папку всех ваших роликов. Скрипт проверяет наличие файлов и одинаковые SHA-256. Он обрабатывает строки CSV, поэтому CSV должен содержать все требуемые ролики.

49 файлов не обязательно означают 49 уникальных видео. Если найдены дубликаты, сначала разберите их локально. Не удаляйте проверки и не уменьшайте ожидаемое количество только для успешного запуска. При подтверждённых дублях оценивайте уникальный набор и явно указывайте его размер.

## 3. Проверить состав до запуска модели

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File deploy\evaluate_windows_cuda.ps1 -Manifest evaluation\manifest\private\manifest-49.json -ExpectedClips 49 -CheckOnly
```

Проверка должна подтвердить ровно 49 записей. При несовпадении количества, повторных ID/хешах, отсутствующих файлах или некорректных метках запуск останавливается. Модель при `-CheckOnly` не загружается.

## 4. Остановить приложение и выполнить базовую оценку

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File deploy\stop.ps1 -WindowsLocalTest
powershell -NoProfile -ExecutionPolicy Bypass -File deploy\evaluate_windows_cuda.ps1 -Manifest evaluation\manifest\private\manifest-49.json -ExpectedClips 49 -RunName cuda-2b-all49-baseline
```

По умолчанию проверяются CW11/CW12. Ролики обрабатываются последовательно. Пока идёт оценка, не запускайте второй экземпляр приложения или другую модель на GPU. У каждого ролика сохраняется исходный дедлайн; общее время зависит от всех видео. По секундному smoke-тесту его предсказать нельзя. При 49 заданиях бюджет до пяти минут на каждое — примерно четыре часа только на задания, плюс загрузка модели и возможное восстановление после ошибок.

Не используйте повторно уже занятое `RunName`: предыдущая оценка должна сохраниться.

Результат: `data/dev_eval/cuda-2b-all49-baseline/summary.json`. Подробности по роликам остаются там же локально. При сбое всей программы проверьте, создана ли итоговая сводка; отсутствие сводки не означает успешную оценку.

## 5. Посмотреть агрегаты

```powershell
$s = Get-Content data\dev_eval\cuda-2b-all49-baseline\summary.json -Raw -Encoding UTF8 | ConvertFrom-Json
$s | Select-Object n,completed,failed,accuracy_all_submissions,completion_rate,latency_sec_max
$s.completed_classification | Format-List
$s.failure_counts | Format-List
```

`n` должно быть 49. `accuracy_all_submissions` учитывает технические сбои как неправильный результат. `completed_classification` показывает accuracy, precision, recall, F1 и TP/FP/TN/FN только завершённых проверок; показывайте её вместе с числом отказов.

Не путайте development-оценку на знакомых роликах с независимым тестом. Для типов и полной оценки нескольких событий нужна дополнительная проверенная разметка. Поле старой ошибки таймкода использует одну метку на ролик и явно помечено как ограниченная диагностика.

## 6. Сравнить следующий вариант

После baseline запустите тот же набор с отдельным профилем:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File deploy\evaluate_windows_cuda.ps1 -Manifest evaluation\manifest\private\manifest-49.json -ExpectedClips 49 -RunName cuda-2b-all49-dense -Profile data\accuracy-experiments\accuracy-improvements-v1\short-dense.json
.\.venv-windows\Scripts\python.exe scripts\accuracy_experiments.py compare data\dev_eval\cuda-2b-all49-baseline\summary.json data\dev_eval\cuda-2b-all49-dense\summary.json
```

Для испытания дополнительного правила укажите, например, `-Rules CW11,CW12,CW01`, сохраните baseline-профиль и новое имя запуска. Это только оценка кандидата, не автоматическое утверждение нормы. Меняйте один фактор за раз. После оценки приложение можно снова запустить через `deploy/start_windows_cuda.ps1`; этот запуск остаётся синтетическим тестовым режимом.
