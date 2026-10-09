# Локальная запись и benchmark STT

Этот сценарий нужен, чтобы сравнить streaming-движки (sherpa-onnx) и Whisper
на собственных или синтетических фразах. Записи и эталонные тексты остаются на
компьютере и не должны попадать в Git. Benchmark не вызывает Codex и не
отправляет аудио в облако.

Историческая версия сравнивала T-One и Whisper. После spike #22 (2026-10-09)
бенчмарк расширен движками T-One / ru-vosk / es-kroko / Nemotron 3.5 320/560 ms
и pseudo-streaming Whisper, а также метриками RTF, аудио-позиции первого
partial, cadence и пиковой памяти. Результаты и рекомендация — в разделах 5–6.

Выбранные движки затем включены в рантайм (issue #23): `app/stt_manager.py`
управляет скачиванием и установкой моделей, а `app/transcribe.py` строит
T-One/transducer-сессию по языку разговора с авто-загрузкой при первом
использовании.

## 1. Подготовить окружение

Нужны `ffmpeg` и backend environment:

```bash
brew install ffmpeg                 # macOS
cd backend
uv sync
```

`sherpa-onnx` — core dependency (с issue #23 streaming STT работает в рантайме),
поэтому отдельная установка через `--with` больше не нужна:

```bash
cd backend
uv run python scripts/run_stt_benchmark.py --help
```

Модели распаковываются в `backend/models/streaming-stt/` (каталог в `.gitignore`).
Ожидаемые подкаталоги (имена как в релизах k2-fsa/sherpa-onnx):

```text
backend/models/streaming-stt/
  sherpa-onnx-streaming-t-one-russian-2025-09-08/
  sherpa-onnx-streaming-zipformer-small-ru-vosk-int8-2025-08-16/
  sherpa-onnx-streaming-zipformer-es-kroko-2025-08-06/
  sherpa-onnx-nemotron-3.5-asr-streaming-0.6b-320ms-int8-2026-06-11/
  sherpa-onnx-nemotron-3.5-asr-streaming-0.6b-560ms-int8-2026-06-11/
```

## 2. Записать реальный корпус локально

На macOS сначала при необходимости посмотрите номера устройств:

```bash
ffmpeg -f avfoundation -list_devices true -i ""
```

По умолчанию скрипт использует `avfoundation` и `:0`. Если микрофон имеет
другой индекс, передайте, например, `--input :1`.

```bash
cd backend
uv run python scripts/record_stt_corpus.py \
  --out /absolute/path/to/luna-stt-corpus \
  --count 10 \
  --duration 8
```

Можно повторять `--prompt` для своего набора фраз. После каждого клипа скрипт
попросит ввести точный эталонный текст. Если запись неудачная, оставьте строку
пустой — WAV будет удалён. Результат:

```text
luna-stt-corpus/
  clip-001.wav
  clip-002.wav
  manifest.jsonl
```

Для WebM/Opus конвертируйте локально:

```bash
ffmpeg -i clip.webm -ar 16000 -ac 1 -c:a pcm_s16le clip.wav
```

## 3. Синтетический корпус через локальный TTS

Для compute/latency/грубого WER (фаза (a) из #22) корпус можно сгенерировать
офлайн через встроенные голоса macOS. Эталонный текст известен точно, ничего не
загружается наружу.

```bash
cd backend
uv run python scripts/build_stt_corpus.py \
  --out models/stt-corpus-synthetic
```

Скрипт создаёт 20 клипов: 6 ru (`Milena`), 6 es (`Mónica`), 6 en (`Samantha`) и
2 смешанных ru/en (конкатенация сегментов двумя голосами), и проверяет, что
каждый файл — 16 kHz mono 16-bit PCM. В `manifest.jsonl` попадают `id`,
`audio`, `reference`, `language`, `kind`, `voice`, `duration_seconds`.

Синтетическая речь чище реальной, а эталоны содержат пунктуацию и числа, поэтому
WER здесь — диагностика для сравнения движков между собой, а не абсолютная
точность продукта. Для честного WER/CER повторите на реальном корпусе (раздел 2).

## 4. Запустить автоматическое сравнение

Один движок (для чистых замеров памяти запускайте по одному на процесс):

```bash
cd backend
uv run --with sherpa-onnx python scripts/run_stt_benchmark.py \
  models/stt-corpus-synthetic/manifest.jsonl \
  --provider vosk \
  --output /absolute/path/to/luna-stt-results/vosk.json
```

Все движки разом (кроме памяти — пиковые значения будут отражать самый тяжёлый):

```bash
uv run --with sherpa-onnx python scripts/run_stt_benchmark.py \
  models/stt-corpus-synthetic/manifest.jsonl \
  --provider all \
  --output /absolute/path/to/luna-stt-results/all.json
```

`--provider`: `tone`, `vosk`, `kroko`, `nemotron320`, `nemotron560`,
`pseudo-whisper`, `whisper`, `both` (старое поведение: T-One + Whisper), `all`.

Полезные параметры: `--model-root`, `--chunk-ms` (по умолчанию 100 ms),
`--step-ms`/`--window-ms` (pseudo-Whisper), `--language`, `--num-threads`.
Флаги `--tone-model`/`--tone-tokens` удалены: пути теперь выводятся из
`--model-root`.

Отчёт JSON содержит по каждому клипу текст, WER, CER, `audio_seconds`,
`elapsed_s`, `rtf`, `first_partial_ms` (wall-clock до первого partial),
`first_partial_audio_ms` (позиция в аудио, на которой появился первый partial),
`partial_count`, `partial_cadence_ms`; в summary — `mean`/`median`/p95 и
`peak_rss_mb` процесса. `peak_rss_mb` пишется только для in-process
sherpa-onnx движков; для Whisper-путей (`whisper-server`) он `null`.

WER/CER нормализуются: lower-case, удаление диакритики (é→e), `ё`→`е`, удаление
всей Unicode-пунктуации (включая `¿¡`). Это убирает шум от пунктуации, но числа,
написанные словами, всё равно дают завышенный WER.

## 5. Результаты spike #22 (2026-10-09)

Железо: MacBook Air (Mac14,2), Apple M2, 16 ГБ RAM, macOS. Софт: `sherpa-onnx`
1.13.8 (CPU provider), локальный `whisper-server` с `ggml-small.bin`, `ffmpeg`.
Корпус: синтетический (раздел 3), 20 клипов, 6 ru / 6 es / 6 en / 2 mixed.
Один прогон на движок, feed 100 ms, `num-threads=8`.

Колонки: **WER/CER** — средние по клипам языка; **RTF** — `decode time / audio`
без загрузки модели; **1-й partial (аудио)** — сколько секунд аудио было
скормлено к моменту первого непустого partial (в burst-прогоне это нижняя
оценка отзывчивости, не wall-clock: реальное время = позиция в аудио + сеть/
захват + compute); **Load** — загрузка модели; **Peak RSS** — пик памяти
процесса.

### Русский (n=6)

| Движок | WER | CER | RTF | 1-й partial (аудио) | Load | Peak RSS |
|---|---|---|---|---|---|---|
| **T-One CTC** | **0.236** | **0.033** | 0.052 | 1.0 s | 0.73 s | 643 MB |
| vosk zipformer small int8 | 0.336 | 0.149 | **0.032** | 0.8 s | 0.60 s | **174 MB** |
| Nemotron 3.5 320 ms | 0.355 | 0.192 | 0.237 | 1.05 s | 0.97 s | 1212 MB |
| Nemotron 3.5 560 ms | 0.404 | 0.205 | 0.179 | 1.28 s | 0.96 s | 1209 MB |
| pseudo-whisper | 0.268 | 0.161 | 0.730 | 1.0 s | — | — |
| Whisper batch (baseline) | 0.268 | 0.161 | 0.203 | — | — | — |

### Испанский (n=6)

| Движок | WER | CER | RTF | 1-й partial (аудио) | Load | Peak RSS |
|---|---|---|---|---|---|---|
| Nemotron 3.5 320 ms | **0.166** | **0.125** | 0.215 | 0.95 s | 0.97 s | 1212 MB |
| es-kroko zipformer | 0.225 | 0.211 | **0.032** | 1.5 s | 1.25 s | **458 MB** |
| Nemotron 3.5 560 ms | 0.221 | 0.180 | 0.169 | 0.9 s | 0.96 s | 1209 MB |
| pseudo-whisper | 0.126 | 0.120 | 0.745 | 1.0 s | — | — |
| Whisper batch (baseline) | **0.126** | **0.120** | 0.210 | — | — | — |

### Английский (n=6)

| Движок | WER | CER | RTF | 1-й partial (аудио) |
|---|---|---|---|---|
| Nemotron 3.5 320 ms | **0.152** | **0.109** | 0.231 | 1.05 s |
| Nemotron 3.5 560 ms | 0.179 | 0.148 | **0.147** | 1.2 s |
| pseudo-whisper | 0.108 | 0.118 | 0.704 | 1.0 s |
| Whisper batch (baseline) | **0.108** | 0.118 | 0.177 | — |

Отдельного лёгкого streaming-движка для английского в этом наборе нет;
покрывает только Nemotron (или batch Whisper).

### Смешанный ru/en (n=2)

| Движок | WER | CER | RTF | 1-й partial (аудио) |
|---|---|---|---|---|
| Nemotron 3.5 560 ms | **0.365** | **0.242** | 0.173 | 1.3 s |
| Nemotron 3.5 320 ms | 0.675 | 0.376 | 0.205 | 1.1 s |
| pseudo-whisper | 0.548 | 0.435 | 0.989 | 1.0 s |
| Whisper batch (baseline) | 0.548 | 0.435 | 0.246 | — |

Code-switching плохо даётся всем движкам; 560 ms Nemotron заметно лучше 320 ms.
Вывод по mixed опирается всего на 2 клипа.

### Language prompt для Nemotron (Python API)

В `sherpa-onnx` 1.13.8 per-stream язык задаётся так:

```python
stream = recognizer.create_stream()
stream.set_option("language", "ru")   # "ru" | "es" | "en" | "auto"
```

Важно: `stream.has_option("language")` возвращает `False` (метод не отражает эту
опцию), поэтому проверять нужно через `try/except`, а не через `has_option`.
Бенчмарк пишет в каждый клип `language_option`; прогон подтвердил, что для ru/es/en
клипов применились `ru`/`es`/`en`, а для mixed — `auto`. Ошибочный язык заметно
ухудшает результат: ru-клип `0.wav` из bundle vosk с `language="en"` распознаётся
как «Я тебя» вместо «Я тебя люблю», `ru` и `auto` дают корректный текст.

### Ограничения замеров

- Синтетический TTS-корпус, один прогон, n=6 (mixed n=2) — это диагностика, а не
  SLA и не финальный рейтинг моделей.
- Кормление аудио идёт burst-режимом, без реального реального времени, поэтому
  RTF и compute-время показывают стоимость декодирования, а не задержку в
  живом стриме. Отдельно не измерялись параллельная нагрузка и CPU при
  нескольких сессиях.
- `peak_rss_mb` для Whisper-путей не измеряется (работа в отдельном
  `whisper-server`); для Nemotron включает ~630 МБ int8-энкодера.
- pseudo-whisper в таблицах показывает WER финального текста, который по
  построению равен batch Whisper (финал пересинтезируется из полного файла);
  качество промежуточных partials этой метрикой не измеряется.
- По WER/CER ни один streaming-движок из набора не лучше batch Whisper на
  es и en. На ru T-One обходит batch Whisper на этом корпусе (WER 0.236 против
  0.268, CER 0.033 против 0.161).

## 6. Рекомендация D7 / D10

**D7 — движок.** Language routing, а не одна модель на всё:

- **ru (по умолчанию): T-One CTC** — лучшая точность на этом корпусе
  (WER 0.236, CER 0.033), уже интегрирован в приложение и под Apache-2.0;
  умеренный след (RTF 0.052, 643 МБ peak). **vosk small int8** — лёгкая
  альтернатива (174 МБ, RTF 0.032), но заметно хуже точность (WER 0.336,
  CER 0.149).
- **es: es-kroko** для лёгкого варианта (458 МБ, RTF 0.032) или **Nemotron
  320 ms** при желании лучшей точности (WER 0.166 vs 0.225) ценой ~7× CPU и
  ~2.6× RAM. Batch Whisper (WER 0.126) остаётся точнее обоих.
- **en:** отдельного лёгкого кандидата нет; streaming — только Nemotron
  (WER 0.152–0.179), batch Whisper точнее (0.108).
- **mixed/code-switch:** если важен один мультиязычный движок — **Nemotron
  3.5 560 ms** (mixed WER 0.365, es 0.221, en 0.179, ru 0.404), ценой ~1.2 ГБ
  peak и RTF 0.17. Для ru он хуже T-One.
- **pseudo-whisper** не дефолт (RTF 0.73–0.99, блокирует CPU); только явный
  opt-in fallback там, где streaming-модель не установлена.

**D10 — CPU budget.** Лёгкие движки (T-One ru RTF 0.052 / 643 МБ; kroko es
RTF 0.032 / 458 МБ) позволяют держать streaming включённым по умолчанию.
Nemotron (RTF 0.17–0.24, peak ~1.2 ГБ) должен быть opt-in и загружаться лениво.
pseudo-whisper — только явный fallback. Эти числа — burst-decode RTF на M2;
отдельного замера при нескольких одновременных сессиях нет, поэтому «по
умолчанию» относится к одному пользователю.

## 7. Лицензии кандидатов

Проверено 2026-10-09. В локальных bundle собственный `LICENSE` есть только у
T-One; остальные сведения — по model card.

| Модель | Лицензия | Источник |
|---|---|---|
| T-One CTC (ru) | Apache-2.0 | `LICENSE` в bundle |
| vosk zipformer small ru (alphacep) | Apache-2.0 | https://huggingface.co/alphacep/vosk-model-small-streaming-ru |
| es-kroko (Banafo/Kroko-ASR) | CC-BY-SA (community) | https://huggingface.co/Banafo/Kroko-ASR |
| Nemotron 3.5 ASR streaming 0.6B | OpenMDW-1.1 | https://huggingface.co/nvidia/nemotron-3.5-asr-streaming-0.6b |
| sherpa-onnx runtime | Apache-2.0 | upstream |

Для локального single-user инструмента все варианты пригодны. Перед
распространением продукта: у es-kroko действует share-alike CC-BY-SA (нужна
атрибуция; коммерческая/OEM-лицензия отдельная), у Nemotron — условия
OpenMDW-1.1 (атрибуция). Лицензии, взятые с model card, стоит перепроверить в
самих репозиториях перед релизом.

## 8. Рекомендуемый состав реального корпуса

Для первого вывода достаточно 10–30 фраз:

- короткие команды и обычные предложения;
- числа, даты, имена и термины проекта;
- естественные паузы;
- тихая речь и умеренный бытовой шум;
- две разные дикции/скорости речи, если это возможно.

Первые результаты лучше считать диагностикой. Для решения о замене
authoritative Whisper повторите корпус несколько раз и отдельно проверьте
browser/WebSocket streaming smoke.

## Приватность и очистка

Корпус содержит голос и эталонные тексты. Храните его вне репозитория, не
публикуйте и удалите после эксперимента обычным способом вашей ОС. Скрипты не
загружают записи и не добавляют их в Git автоматически.
