# Conversation Core Protocol

## Voice of Luna — Personal Voice Interface

# Назначение

Это не сценарий тренировки, а минимальный протокол естественного voice turn-taking. Он должен быть одинаковым для обычного разговора и будущих plugins.

# Состояния

`IDLE → LISTENING → TRANSCRIBING → THINKING → SPEAKING → LISTENING`.

Пользователь может выбрать `PAUSED` из любого активного состояния и `ENDED` из любого состояния. Ошибка переводит только текущий turn в `ERROR`; разговор остаётся доступным для retry или завершения.

# Инварианты

- Модель вызывается только после подтверждённой непустой текстовой реплики.
- Молчание не является сообщением и не вызывает ответ модели.
- Текст ассистента не попадает в TTS до завершения LLM turn.
- Начало речи пользователя при `SPEAKING` сначала останавливает playback, затем открывает новый turn.
- Transcript принадлежит пользователю: его можно просмотреть или удалить; raw audio не сохраняется по умолчанию.
- Внешний plugin не может изменить эти инварианты.

# Turn contract

Core передаёт модели system instruction для краткого голосового диалога, краткую историю и текущую user utterance. Результат базового режима — только текст assistant response. Выбранный native plugin может дополнительно объявить `ToolSpec`; app-server передаёт tool request обратно через bidirectional JSON-RPC, а backend возвращает structured `contentItems` в тот же turn.

# Plugin hooks and tools

Core и plugin разделены строго. Core владеет только нейтральным turn/audio
протоколом и generic transport. Plugin владеет своими настройками, памятью,
панелью, предметными правилами исследования и артефактами доказательств.
`Conversation` не должен содержать поля конкретного plugin (например,
`project_root` или `github_repository`); настройки передаются как opaque
структура и интерпретируются только активным plugin.

Панель плагина может отправлять generic action (`refresh`, `forget` и т.п.).
Результат и побочные эффекты принадлежат plugin state; core только применяет
общий timeout/error boundary и возвращает structured result.

Если plugin возвращает evidence, host связывает его с текущим turn. Для
Project Room это относительный путь и ограниченный диапазон строк; evidence
не проговаривается через TTS и не заменяет чтение файла.

Plugin может добавить prompt context в `beforeTurn`, сохранить собственную заметку в `afterTurn`, отобразить панель через `renderPanel` или объявить native tools через `tools()`. Core применяет timeout и redaction; hook или tool не может задерживать аудио-путь бесконечно. Tool получает capability-oriented context: raw audio, OAuth tokens, process handles и произвольный shell не передаются обычным контрактом. Плагины остаются trusted in-process code, а не security sandbox.

## Live interim transcript

Mic-путь остаётся batch-авторитетным: финальный текст всегда приходит в
`transcript` из `handle_audio`. Когда локальная streaming-модель установлена и
live-режим включён, host дополнительно шлёт `stt_partial` с полем `text` и
`interim: true` по мере распознавания речи. Браузер показывает эти partials в
ленте как промежуточную user-запись и при получении `transcript` заменяет её
авторитетным текстом (полная замена, без diffing). Partial никогда не попадает
в `conversation.turns`, не запускает LLM и не является сообщением; при barge-in,
ошибке или пустом аудио он очищается. Live-режим включается флагом
`live_transcript` в `ready`/`set_settings` и включён по умолчанию; окружение
`VOICE_OF_LUNA_STREAMING_STT=0` (и legacy `VOICE_OF_LUNA_TONE_STREAMING=0`)
выключает его как аварийный переключатель. Движок выбирается по языку сессии:
T-One для ru, es-kroko для es, Nemotron — только если уже установлен. Модели
управляются локальным streaming-STT manager-ом (`/api/stt/models`) и
скачиваются автоматически при первом использовании
(`VOICE_OF_LUNA_STT_AUTODOWNLOAD=0` отключает авто-загрузку). При отсутствии
streaming-модели режим деградирует до обычного batch Whisper, а `ready`
возвращает `live_transcript_model` со статусом (`ready`/`not_installed`/
`downloading`/`error`).

## Mixed-language and bilingual speech routing

Core остаётся domain-neutral: он не знает, зачем плагину два языка, но умеет
маршрутизировать текст по языкам. По умолчанию (`VOICE_OF_LUNA_SEGMENT_ROUTING`
не `0`) каждый уже выделенный речевой сегмент разбивается на языковые прогоны и
каждый прогон синтезируется своим голосом; прогоны доставляются как отдельные
`audio_chunk` в исходном порядке. Классификатор намеренно консервативен: только
уверенные признаки (кириллица, испанская орфография, фиксированные списки
английских/испанских слов) переключают голос, а короткие общие слова, цифры и
неизвестные скрипты наследуют соседний прогон. Поэтому «включи Docker container
и проверь build» произносится русским голосом и английскими словами в
английском голосе, а не транслитерацией.

Плагин может объявить второй язык через `explanation_locale`: например
`response_locale_override = "es-ES"` и `explanation_locale = "ru-RU"` дают
ответ на целевом языке и объяснение на родном. Host подбирает отдельный
установленный голос для этого языка; если подходящего голоса нет или фича
выключена, поведение деградирует к прежнему одноголосому пути, а не падает.
Если выбранный голос объявляет `code_switching: native`, текст отправляется
ему как есть без сегментации (кроме случая, когда плагин явно запросил
`explanation_locale`).

Пути с одним клипом (`resynthesize`-replay, plugin-approved speech, HTML
fallback) сохраняют контракт одного артефакта: прогоны синтезируются локально и
склеиваются ffmpeg-ом; если склейка невозможна, возвращается явная ошибка, а не
подмена всего текста одним голосом. Явный `re-voice` (`voice` в запросе)
применяет выбранный голос ко всему тексту и сегментацию не выполняет.

## Optional delivery contracts

По умолчанию ответ остаётся streaming-совместимым: host может передавать model
deltas в обычный TTS-путь. Плагин, которому нужна проверка полного ответа до
доставки, может объявить `delivery_mode=gated`. Тогда host буферизует законченный
model response и вызывает `validate_response`; только `allow` или `replace`
попадают в UI/TTS. `reject`, timeout или ошибка validator-а не выпускают исходный
текст. В историю и `afterTurn` записывается только разрешённый текст.

Браузер может сообщать lifecycle уже отправленного audio clip через коррелированные
`output_event`: `started`, `completed`, `interrupted` или `failed`, с `turn_id` и
`clip_id`. `completed` подтверждает полное воспроизведение clip; interruption не
доказывает, до какого слова пользователь его услышал.

## Playback controls

Пользователь может поставить воспроизведение на паузу и возобновить его. `paused` —
это состояние только аудио-плеера: LLM turn и синтез продолжают работу, а входящие
audio chunks буферизуются и проигрываются после resume. Для Web Audio resume
продолжается с сохранённого sample-offset; для `Audio`-element fallback используется
нативный pause/play, то есть возобновление также происходит с текущей позиции clip.
Barge-in (`stop`) немедленно прекращает и приостановленное воспроизведение.

Повтор (`replay`) и повтор с другим голосом (`re-voice`) всегда пересинтезируют
авторитетный текст assistant turn через полный `SpeechSynthesizer`-стек
(`POST /api/conversations/{id}/turns/{n}/resynthesize`), не вызывая Codex и не
создавая новый turn. Re-voice эфемерен: текст и голос сессии не меняются, пока не
запрошено `set_default`. Связанные `output_event` несут metadata `replay: true`,
чтобы plugin мог отличать повтор от первой доставки. Серверные audio clips
одноразовые и не сохраняются для последующего replay.

Плагин может запросить воспроизведение заранее утверждённого текста отдельной
generic host capability. Такая операция не вызывает LLM и не добавляет synthetic
assistant turn; смысл replay и immutable source остаётся собственностью плагина.

Внешний Python-пакет объявляется через entry-point group `voice_of_luna.plugins`. Его стабильная поверхность host-а — `app.plugin_api`: `Plugin`, turn contexts/results и tool contracts. Пакет не должен импортировать `PluginManager`, web handlers или другие private host-модули; смысл workflow, его состояния и релизы принадлежат отдельному репозиторию плагина.

Project Room — первый first-party tool plugin. Он сам владеет выбранным Git-root,
project card, freshness, repository tools, evidence и project-scoped memory.
Core предоставляет ему только generic plugin settings, capability context,
namespaced storage и dynamic-tool transport. `github.create_issue` требует
одноразового `external.write` approval через
`POST /api/conversations/{id}/tool-approval`; approval живёт 60 секунд и
потребляется одним вызовом.

# Минимальные тесты

Пауза пользователя не запускает LLM. Barge-in очищает playback. Одновременные user turns сериализуются. Ошибка Codex runtime видна UI и не превращается в фиктивный ответ. Удаление разговора удаляет его transcript/events. Plugin, который пытается прочитать credential или raw audio, не получает такой capability.
