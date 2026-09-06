# MVP & Implementation Plan

## Voice of Luna — Personal Voice Interface

# Результат MVP

Один пользователь на своей машине открывает local HTTPS URL, разговаривает с сильной моделью голосом и использует уже авторизованный Codex runtime. Он может думать молча, перебивать ответ, видеть понятные статусы и удалить transcript. Личная тренировка не является функцией MVP.

# Этап 0 — Neutral skeleton

Создать FastAPI backend с server-rendered HTML и htmx, health endpoint, runtime status и local conversation model. Добавить безопасные defaults: localhost only, нет telemetry, нет raw-audio persistence, нет secret в `.env` или frontend.

# Этап 1 — Codex bridge spike

Проверить установленный `codex app-server`: protocol handshake, ephemeral thread, один text `turn/start`, получение итоговой agent message, shutdown и безопасную ошибку без ответа. Bridge не делает OAuth сам и не читает credential files. Он использует уже выполненный `codex login` на машине владельца.

# Этап 2 — Text conversation shell

Подключить UI к local backend: создать conversation, отправить текст, получить ответ и удалить историю. Этот путь должен полностью работать до микрофона. Добавить contract tests с fake provider, а реальный Codex test оставить opt-in.

# Этап 3 — Voice round trip

Добавить microphone → STT → text turn → TTS → speaker. Сначала допустимы committed chunks; streaming добавляется только после замера latency. Записать timings endpointing, STT, Codex и first audio.

# Этап 4 — Realtime UX

Добавить states «слушаю / думаю / говорю», patient silence, mute, stop speaking, barge-in, понятные errors и mobile smoke. WebSocket/reconnect не создаёт второй conversation.

# Этап 5 — Plugin SDK design check

После работающего voice loop реализовать manifest validation и один безопасный prompt-only example plugin. Не переносить личный training protocol, пока не доказана польза общего ядра.

# Definition of Done

- Local bridge работает с текущей авторизацией Codex, не экспортируя токены.
- Обычный текстовый turn проходит end-to-end и даёт видимую ошибку при недоступном runtime.
- Voice turn работает без клавиатуры после выдачи browser permissions.
- Barge-in останавливает TTS локально.
- Пользователь может удалить transcript; raw audio не остаётся в storage.
- Модель, аудио и plugins не меняют базовый trust boundary.

# Основные риски

App-server помечен экспериментальным и его schema может меняться; bridge должен pin/check version и иметь ясную деградацию в text-only local mode. Personal Codex subscription имеет свои usage limits, поэтому UI не обещает «безлимитный API». У браузерного STT/TTS есть platform/privacy ограничения; конкретные provider решения принимаются после замера voice loop.
