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
