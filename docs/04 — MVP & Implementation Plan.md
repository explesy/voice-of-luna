# MVP & Implementation Plan

## Voice Trainer — Web App

# Цель MVP

Доказать, что web-приложение может ощущаться как естественный голосовой разговор и при этом надёжнее стандартного Live-режима соблюдать детерминированный training protocol. Первая версия ориентирована на одного пользователя и один реальный сценарий тренировок.

# Этап 0 — Skeleton

Создать monorepo или два простых приложения: frontend React/TypeScript и backend FastAPI. Добавить Docker development environment, конфигурацию через environment variables, health endpoint и базовый session model. Настроить HTTPS/WSS для среды, где тестируется настоящий microphone input.

# Этап 0.5 — Codex-connected spike

До голосового контура проверить два LLM adapter-а на одном коротком structured turn. Для `LocalCodexLanguageModelProvider` подключить локальный Codex companion к личной ChatGPT/Codex-учётной записи через его поддерживаемый login flow и убедиться, что browser/backend не видят OAuth token. Проверить restart, истёкшую короткую сессию/refresh и явное отключение. Не писать собственный OpenAI OAuth client и не делать этот режим доступным с удалённого сервера. Если контракт app-server нестабилен или не документирован, зафиксировать результат и продолжить MVP через API-key adapter.

# Этап 1 — Voice round trip

Получить microphone stream в браузере, передать его backend, сделать STT, отправить распознанный текст в text LLM, синтезировать ответ через TTS и проиграть его в браузере. На этом этапе protocol может быть минимальным. Главный результат — измеряемый полный voice loop и timing каждого участка.

# Этап 2 — Realtime UX

Добавить automatic endpointing, partial/final transcript events, streaming response, audio queue и visual states «слушаю / думаю / говорю». Реализовать barge-in так, чтобы пользователь мог перебить TTS. Добавить reconnect и обработку ошибок микрофона/провайдера.

# Этап 3 — Conversation Controller

Реализовать конечный автомат и server-owned session clock. Добавить structured LLM output, Protocol Validator, counters, unfinished requirements и запрет недопустимых transitions. Создать unit tests для всех переходов до подключения сложного training content.

# Этап 4 — Training protocol

Перенести первый реальный сценарий: scene → user attempt → ограниченная reflection → return to scene → exact replay → debrief. Добавить patient silence profile, точный replay из сохранённого turn и Repeat last line. Протокол должен работать без изменения core voice transport.

# Этап 5 — Persistence и debrief

Сохранять sessions, turns и events. Добавить post-session summary и страницу просмотра session transcript/log. Кнопка Report protocol issue должна помечать конкретный turn/state snapshot. Raw audio по умолчанию не хранить.

# Этап 6 — Usability pass

Сделать минимальный session screen удобным на desktop и mobile browser. Проверить permissions, headphones/speaker behavior, фоновые вкладки, screen lock ограничения на мобильных браузерах и разные микрофоны. Добавить простые настройки голоса, длительности и silence profile.

# Definition of Done для первой полезной версии

Один пользователь может открыть HTTPS URL и провести полноценную голосовую тренировку без клавиатуры. Микрофон/STT/LLM/TTS работают в одном непрерывном flow. Session timer и state принадлежат backend. Модель не может самовольно завершить сессию или пропустить обязательный этап. Replay точный. Несколько секунд размышления не вызывают нежелательный ответ. Barge-in прекращает TTS. После сессии доступен debrief и воспроизводимый event log. В personal mode запрос проходит через локальный Codex companion, а OAuth credential не попадает в browser, session database или удалённый server.

# Инженерные критерии

Нет provider API keys во frontend bundle. Все model names конфигурируются. Domain controller тестируется без реальных provider calls. Каждое provider обращение имеет timeout и понятную ошибку. WebSocket reconnect не создаёт вторую параллельную сессию. Rejected model actions логируются. Удаление сессии удаляет persistent transcript/events согласно выбранной retention policy. Для Codex-connected adapter-а есть тест, доказывающий, что OAuth token не сериализуется ни в API response, ни в event payload, ни в application logs.

# Первый backlog после MVP

Project presets и редактор protocol config. Загрузка project context из внешних документов. Более умный retrieval прошлых тренировок. Несколько TTS/STT providers. Автоматическая оценка protocol adherence. Экспорт session summary. Пользовательские аккаунты. PWA-install. Улучшенная мобильная работа. Cost dashboard.

# Что пока намеренно отложено

Автоматизация consumer ChatGPT web UI. Native macOS/iOS/Android clients. Мультипользовательская коммерческая инфраструктура. Сложная vector database. Долговременная автономная память без контроля пользователя. Fine-tuning до появления данных, показывающих, что prompt \+ controller недостаточны.

# Первые технические задачи

Сначала собрать минимальный microphone → backend WebSocket → echo/playback path. Затем подключить STT и проверить endpointing на реальной речи и паузах. После этого добавить text LLM и TTS. Только когда latency голосового loop приемлема, добавить finite-state controller и перенести реальный training protocol. Такой порядок отделяет проблемы realtime audio от проблем поведения модели.

# Главные риски

Слишком большая задержка между turns. Endpointing обрезает размышления пользователя. Mobile browser ограничивает audio в фоне. Streaming TTS плохо отменяется при interrupt. LLM генерирует удобочитаемый текст, но нарушает machine contract. Context становится слишком большим и дорогим. Для Codex-connected mode дополнительно есть риск изменения или отсутствия подходящего app-server контракта и исчерпания лимита личной подписки; поэтому он должен быть feature-flagged и иметь API-key fallback. Эти риски должны измеряться отдельными timings и fixture tests, а не маскироваться дополнительными prompt-инструкциями.

# Ключевой эксперимент

Сравнить несколько настоящих тренировок через новый web prototype с предыдущим Live-подходом. Главная метрика эксперимента — не субъективная «умность» модели сама по себе, а число protocol violations, качество пауз/replay, ощущение естественности голоса и способность провести запланированную структуру от начала до конца.  
