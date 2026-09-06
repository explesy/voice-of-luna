# Technical Architecture

## Voice Trainer — Web App

# Архитектурная цель

Сделать low-latency voice shell над сильной текстовой LLM, при котором детерминированный application controller управляет протоколом, а STT/LLM/TTS являются заменяемыми сервисами.

# Рекомендуемый стек

Frontend: React \+ TypeScript \+ Vite. Backend: Python \+ FastAPI. Realtime transport: WebSocket для control events и аудио в MVP. Browser audio: getUserMedia \+ AudioWorklet предпочтительно; MediaRecorder допустим для первого spike. Persistence: SQLite для локального single-user prototype, затем PostgreSQL без изменения доменной модели. Deployment: Docker, HTTPS обязателен для browser microphone permissions.

# Компоненты frontend

Session screen управляет разрешениями микрофона, таймером, визуальным статусом, playback и пользовательскими controls. Audio capture преобразует microphone stream в формат, который ожидает backend/STT. Playback queue получает TTS chunks, воспроизводит их по порядку и умеет мгновенно очищаться при barge-in. WebSocket client передаёт аудио и события и принимает transcripts, state updates, text responses и audio chunks.

# Компоненты backend

Session Manager создаёт и восстанавливает сессию. Conversation Controller хранит state machine и единолично разрешает переходы между фазами. Context Builder собирает project instructions, session state и минимальный релевантный history. LLM Adapter отправляет текстовые запросы и требует structured output. Protocol Validator проверяет действие модели и при необходимости отклоняет или исправляет переход. STT Adapter отвечает за streaming/committed transcription. TTS Adapter синтезирует разрешённый spoken text. Event Logger пишет последовательность событий для отладки и debrief.

# Основной поток данных

Browser microphone → WebSocket audio frames → STT → committed user transcript → Conversation Controller → Context Builder → text LLM → structured model output → Protocol Validator → approved spoken text/action → streaming TTS → WebSocket audio chunks → browser speaker.

# Разделение ответственности

LLM решает, что содержательно сказать в рамках разрешённого шага. Controller решает, можно ли говорить, какой сейчас этап, сколько осталось времени, разрешён ли replay, можно ли задать ещё один вопрос и можно ли завершить сессию. TTS никогда не получает текст до protocol validation. STT не меняет state machine напрямую: он публикует user turn events.

# Provider abstraction

Используются интерфейсы SpeechToTextProvider, LanguageModelProvider и TextToSpeechProvider. Имена конкретных моделей задаются environment/config. Первым можно реализовать один официальный API-провайдер, но доменные компоненты не должны импортировать provider-specific SDK напрямую. Consumer ChatGPT browser UI не используется как backend-интеграция.

Для LLM предусмотрены два изолированных adapter-а:

1. `OpenAIApiLanguageModelProvider` — backend получает server-side API key из environment; это единственный вариант для обычного удалённого deployment и будущих отдельных пользователей.
2. `LocalCodexLanguageModelProvider` — backend личного MVP общается только с локальным Codex companion/app-server. Companion начинает OAuth/PKCE login на устройстве владельца и хранит refresh credential вне web-процесса; на каждый turn передаёт runtime лишь краткоживущую сессию. WebSocket браузера знает только session id и состояние тренировки.

Оба adapter-а возвращают один и тот же structured LLM contract. Поэтому Conversation Controller, Validator и session log не зависят от способа оплаты или авторизации модели.

## Codex-connected personal mode

Этот режим предназначен только для одного доверенного оператора на его машине. Локальный companion слушает loopback/Unix socket, принимает запросы исключительно от local backend и не имеет публичного HTTP endpoint. Он запускает поддерживаемый Codex runtime вместо эмуляции ChatGPT сайта. Авторизацию нельзя копировать в SQLite, логи, Docker image, frontend bundle или remote deployment; нельзя передавать refresh token через WebSocket.

Перед реализацией нужно подтвердить на актуальной версии Codex app-server его поддерживаемый transport, structured output и lifecycle/refresh. Если этот spike не даёт стабильного поддерживаемого контракта, режим остаётся выключенным, а MVP идёт через `OpenAIApiLanguageModelProvider` — без самодельного OAuth-клиента.

# WebSocket protocol

Минимальные client events: session.start, audio.chunk, audio.commit, user.interrupt, playback.finished, session.pause, session.resume, session.end, replay.request, issue.report. Минимальные server events: session.state, transcript.partial, transcript.final, assistant.text, audio.chunk, audio.end, protocol.warning, debrief.ready, error.

# HTTP API

POST /api/sessions создаёт сессию и возвращает id/config. GET /api/sessions/{id} возвращает metadata и summary. GET /api/sessions/{id}/events используется для отладки. DELETE /api/sessions/{id} удаляет сохранённые данные. WebSocket /ws/sessions/{id} обслуживает realtime loop.

# LLM contract

Модель должна возвращать structured object, а не только свободный текст. Базовая схема:  
spoken\_text: string  
proposed\_action: enum  
reason\_code: short enum/string  
memory\_note: optional string  
Никакой переход state machine не выполняется непосредственно из natural-language текста.

# Session state

Минимальное состояние содержит session\_id, project\_id, phase, step, started\_at, remaining\_time, reflection\_questions\_used, replay\_source\_turn\_id, unfinished\_requirements, last\_user\_turn\_id, last\_assistant\_turn\_id, playback\_state и protocol\_flags. Время рассчитывается приложением, а не самой LLM.

# Контекст и память

Prompt собирается слоями: global safety/behavior → project protocol → current session goal → machine-readable state → небольшой relevant history → последние turns → текущая user utterance. Большую полную историю не следует пересылать каждый turn. После сессии отдельный summarizer может создать компактную memory entry.

# Latency

STT должен работать streaming или быстро обрабатывать committed utterance. LLM response следует стримить, но TTS можно запускать только после получения фразы, которая уже прошла нужную validation boundary. Ответы в active scene специально короткие. Persistent connections и provider clients переиспользуются. Измеряются отдельно endpointing delay, STT latency, LLM first-token/first-sentence latency, TTS first-audio latency и total turn latency.

# Barge-in implementation

Frontend отслеживает voice activity даже во время playback. При обнаружении речи он локально останавливает audio queue и отправляет user.interrupt. Backend отменяет/игнорирует остаток текущего TTS stream, фиксирует сколько ответа было фактически воспроизведено и снова принимает microphone stream.

# Persistence

Для MVP достаточно таблиц/projects, sessions, turns и events. Turn хранит role, transcript, approved spoken text, timestamps и interrupted flag. Event хранит тип, payload и timestamp. Raw audio хранить не обязательно. Protocol violations и rejected model actions должны попадать в events.

# Security

Provider API keys находятся только на server. OAuth access/refresh tokens Codex находятся только у локального companion и не считаются данными сессии Voice Trainer. Все realtime соединения работают через HTTPS/WSS; связь backend → companion остаётся loopback/Unix socket. Для личного MVP можно начать с простого access token или single-user auth; перед публичным запуском нужен нормальный authentication, CSRF/CORS policy, rate limiting и data retention policy. Публичный запуск не должен переиспользовать личный Codex OAuth profile как общий серверный credential.

# Observability

Каждый turn получает correlation id. Логируются state before, user transcript, model structured output, validator decision, state after, timings и interruption/replay events. Кнопка Report protocol issue помечает текущий turn для последующего анализа.

# Тестирование

Unit tests покрывают state transitions и validator. Contract tests проверяют LLM schema parsing. Integration tests симулируют user turns без реального audio. Отдельные realtime tests проверяют barge-in, reconnect и playback ordering. Ключевые protocol bugs должны воспроизводиться fixture-сессиями.  
