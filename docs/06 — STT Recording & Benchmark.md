# Локальная запись и benchmark STT

Этот сценарий нужен, чтобы сравнить T-One и Whisper на собственных русских
фразах. Записи и эталонные тексты остаются на компьютере и не должны
попадать в Git. Benchmark не вызывает Codex и не отправляет аудио в облако.

## 1. Подготовить окружение

Нужны `ffmpeg` и backend environment:

```bash
brew install ffmpeg                 # macOS
cd backend
uv sync
```

Для T-One используйте официальный bundle с `model.onnx` и `tokens.txt`.
Sherpa остаётся optional dependency и запускается только для этого эксперимента:

```bash
export VOICE_OF_LUNA_TONE_MODEL=/absolute/path/to/model.onnx
export VOICE_OF_LUNA_TONE_TOKENS=/absolute/path/to/tokens.txt
```

## 2. Записать корпус локально

На macOS сначала при необходимости посмотрите номера устройств:

```bash
ffmpeg -f avfoundation -list_devices true -i ""
```

По умолчанию скрипт использует `avfoundation` и `:0`. Если микрофон имеет
другой индекс, передайте, например, `--input :1`.

Запуск:

```bash
cd backend
uv run python scripts/record_stt_corpus.py \
  --out /absolute/path/to/luna-stt-corpus \
  --count 10 \
  --duration 8
```

Для Linux обычно используется `--format pulse --input default`, для Windows —
`--format dshow --input 'audio=NAME'`. Можно повторять `--prompt` для своего
набора фраз:

```bash
uv run python scripts/record_stt_corpus.py \
  --out /absolute/path/to/luna-stt-corpus \
  --prompt "Скажи эту фразу обычным голосом." \
  --prompt "Теперь сделай короткую паузу перед вторым предложением."
```

После каждого клипа скрипт попросит ввести точный эталонный текст. Это единственная
часть, которую важно проверить вручную: WER и CER считаются относительно этой
строки. Если запись неудачная, оставьте строку пустой — WAV будет удалён.

Результат:

```text
luna-stt-corpus/
  clip-001.wav
  clip-002.wav
  manifest.jsonl
```

### Вариант через браузер

Если удобнее записывать кнопкой в браузере, используйте любой recorder,
который позволяет сразу скачать файл. Для приватных фраз лучше не загружать
голос на сторонний сайт: запись через браузер на локальной странице или
обычный системный диктофон безопаснее. Если сервис отдаёт WebM/Opus, сразу
преобразуйте файл локально:

```bash
ffmpeg -i clip.webm -ar 16000 -ac 1 -c:a pcm_s16le clip.wav
```

Затем добавьте строку в `manifest.jsonl` рядом с WAV:

```json
{"id":"clip-001","audio":"clip.wav","reference":"Точный текст, который был произнесён","sample_rate":16000}
```

Внешний сервис в таком варианте используется только как средство записи;
распознавание и benchmark всё равно выполняются локально.

## 3. Запустить автоматическое сравнение

Только T-One:

```bash
cd backend
uv run --with sherpa-onnx python scripts/run_stt_benchmark.py \
  /absolute/path/to/luna-stt-corpus/manifest.jsonl \
  --provider tone \
  --output /absolute/path/to/luna-stt-results/tone.json
```

Whisper использует обычный локальный Whisper HTTP server или `whisper-cli`,
как и само приложение. Сравнение обеих моделей:

```bash
uv run --with sherpa-onnx python scripts/run_stt_benchmark.py \
  /absolute/path/to/luna-stt-corpus/manifest.jsonl \
  --provider both \
  --output /absolute/path/to/luna-stt-results/compare.json
```

В консоль и JSON-отчёт попадут для каждого клипа:

- распознанный текст;
- WER и CER;
- полное время распознавания;
- для T-One — время до первого partial и количество partial-обновлений;
- ошибки конкретной модели, если runtime не настроен.

В summary будут `mean`, `median` и nearest-rank `p95`. Это локальный
эксперимент, а не SLA: указывайте в заметке дату, OS, CPU, версии моделей и
размер корпуса.

## 4. Рекомендуемый состав корпуса

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
