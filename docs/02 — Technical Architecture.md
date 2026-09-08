# Technical Architecture

## Voice of Luna — Personal Voice Interface

# Цель и граница доверия

Voice of Luna — local-first voice shell над сильной текстовой моделью. Browser отвечает за микрофон, визуальное состояние и playback. Local backend управляет turn-taking, хранением истории и адаптерами. Codex app-server отвечает за LLM turn, используя уже авторизованную личную учётную запись владельца.

```text
Browser ── HTTP/WS ── Local backend ── stdio / Unix socket ── Codex app-server
 microphone                  │                                      │
 speaker                     ├── STT / TTS adapters                 └── ChatGPT/Codex account
                             └── local conversation store
```

Browser не получает API keys, OAuth access token или refresh token. Local backend не читает и не копирует credential из Codex home: он использует app-server как runtime. App-server не открывается в сеть; допустимы только stdio или loopback/Unix socket. Публичный deployment с личной Codex-учётной записью запрещён: для него потребуется отдельный API-key provider и полноценные пользовательские аккаунты.

# Стек MVP

- UI: FastAPI templates + htmx; no client-side application framework.
- Local backend: Python + FastAPI.
- Realtime: WebSocket для статусов, transcript events и потоковых аудио-событий; HTTP остаётся text/compatibility fallback.
- Browser audio: `getUserMedia`, `AudioWorklet` for PCM/VAD capture with `MediaRecorder` fallback.
- Persistence: SQLite, только local filesystem. Raw audio по умолчанию не сохраняется.
- Runtime LLM: установленный `codex app-server` через stdio. Версию и schema проверяем при запуске.

# Neutral conversation core

`ConversationService` создаёт разговор, хранит transcript и последовательно проводит один user turn. `VoiceTransport` определяет конец реплики, отправляет transcript и отменяет TTS при barge-in. Core не знает о тренингах, сценах, целях, дебрифе или специальных prompt-ах.

Минимальные состояния: `idle`, `listening`, `transcribing`, `thinking`, `speaking`, `paused`, `error`. `End` разрешён из любого состояния. Начало речи во время `speaking` немедленно останавливает локальный playback и отменяет дальнейшую доставку аудио.

# Provider adapters

`SpeechToTextProvider`, `TextToSpeechProvider` и `LanguageModelProvider` — небольшие интерфейсы без provider-specific типов в core.

`LocalWhisperTranscriber` конвертирует browser recording локальным `ffmpeg` в mono 16 kHz WAV, запускает multilingual Whisper small и возвращает transcript. Исходник, WAV и JSON-результат существуют только на время turn и затем удаляются. TTS может использовать локальные Piper/Silero/macOS voices или opt-in network-backed Edge TTS; фактически использованный engine сохраняется в telemetry. `LocalCodexLanguageModelProvider` запускает app-server, выполняет protocol handshake и переиспользует один ephemeral thread на conversation. Provider отдаёт текст `agentMessage`; native plugin tool results проходят через отдельный capability boundary. Runtime запускается с read-only sandbox и без доступа к этому репозиторию, кроме явно выбранного Project Room root.

`OpenAIApiLanguageModelProvider` остаётся будущим fallback для server deployment. Он не нужен, чтобы запустить personal MVP.

# Plugin boundary and native tool runtime

Plugin — локальный пакет с manifest и одной или несколькими ограниченными точками расширения: `systemPrompt`, `beforeTurn`, `afterTurn`, `renderPanel`, `tools` и `call_tool`. Он получает нормализованный transcript и capability context, но никогда не получает аудио stream, OAuth credential, app-server transport или возможность писать в core database вне своего namespace.

Для native tools используется opt-in `dynamicTools` app-server protocol. `CodexAppServer` остаётся общим bidirectional bridge: server-initiated `item/tool/call` маршрутизируется через `PluginManager`, где проверяются объявление инструмента, permission и timeout. MCP пока остаётся адаптером для внешних интеграций; first-party Project Room не зависит от MCP.

Persistent plugin memory использует один local SQLite/FTS5 файл с namespace по `plugin_id` и scope (`global`, `plugin`, `conversation`). Repository access ограничен realpath выбранного project root и read-only операциями.

Первый реализованный plugin — `Project Room`: его память хранится в plugin-scoped SQLite/FTS5, repository tools работают read-only в выбранном root, а `github.create_issue` требует одноразового approval `external.write` на 60 секунд. Базовый продукт без plugin остаётся полезным; личные тренировочные сценарии по-прежнему не входят в core.

# API, security и проверки

- `GET /healthz` — состояние backend и доступность Codex runtime без персональных данных.
- `GET /api/runtime` — версия app-server, режим и причина недоступности.
- `POST /api/conversations` — создаёт local conversation.
- `POST /api/conversations/{id}/turns` — принимает уже распознанный текст для первого smoke path.
- `DELETE /api/conversations/{id}` — удаляет transcript и events.
- `WS /ws/conversations/{id}` — realtime audio input, transcript/status events, streamed assistant text и TTS chunks.
- `POST /api/conversations/{id}/plugin` — выбирает plugin/mode и, для Project Room, локальный `project_root`.
- `POST /api/conversations/{id}/tool-approval` — выдаёт одноразовое approval для `external.write`.

Логируются только lifecycle events, timings, безопасные технические ошибки и явно сохранённый transcript. OAuth/API secrets, raw audio и полные stdout/stderr app-server не логируются. Contract test выполняет JSON-RPC handshake с установленным app-server и проверяет один короткий text turn только по явному локальному запуску. Browser smoke проверяет permission/error states и то, что transcript не отправляется в URL или local logs без согласия.
