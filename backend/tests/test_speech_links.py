from pathlib import Path
import pytest
from fastapi.testclient import TestClient

from app.main import app, format_terminal_text
from app import main as main_module
from app.speak import sanitize_for_speech, LocalMacOsSpeaker


client = TestClient(app)


def test_sanitize_strips_sources_section() -> None:
    text = (
        "За август заметно выстрелил фреймворк для разработчиков.\n\n"
        "Источники:\n"
        "- [agency-agents](https://github.com/msitarzewski/agency-agents)\n"
        "- [PPPL](https://www.pppl.gov)"
    )
    clean = sanitize_for_speech(text)
    assert clean == "За август заметно выстрелил фреймворк для разработчиков."
    assert "https" not in clean
    assert "agency-agents" not in clean


def test_sanitize_strips_citations_in_parens() -> None:
    # User's actual prompt case 1:
    text = (
        "За август заметно выстрелил [agency-agents](https://github.com/msitarzewski/agency-agents) – "
        "набор специализированных AI–агентов с разными ролями. "
        "(([снимок GitHub Trending](https://github.com/trending), [репозиторий](https://github.com/msitarzewski/agency-agents)))"
    )
    clean = sanitize_for_speech(text)
    assert "agency-agents" in clean
    assert "https" not in clean
    assert "github.com" not in clean
    assert "снимок" not in clean
    assert "репозиторий" not in clean
    assert clean.startswith("За август заметно выстрелил agency-agents – набор специализированных AI–агентов")


def test_sanitize_strips_parenthetical_news_link() -> None:
    # User's actual prompt case 2:
    text = (
        "Сегодня интересная новость из науки: исследователи Принстона испытали ИИ, который умеет "
        "контролировать плазму. ([PPPL](https://www.pppl.gov/news/2026/pacman-ai))"
    )
    clean = sanitize_for_speech(text)
    assert clean == "Сегодня интересная новость из науки: исследователи Принстона испытали ИИ, который умеет контролировать плазму."
    assert "PPPL" not in clean
    assert "https" not in clean


def test_sanitize_inlines_markdown_links() -> None:
    text = "Посмотри проект [FastAPI](https://fastapi.tiangolo.com) для создания быстрых API."
    clean = sanitize_for_speech(text)
    assert clean == "Посмотри проект FastAPI для создания быстрых API."
    assert "https" not in clean
    assert "[" not in clean and "]" not in clean


def test_sanitize_removes_bare_urls_and_markdown() -> None:
    text = "**Внимание**: подробности на https://example.com/info/test?q=1 в блоге."
    clean = sanitize_for_speech(text)
    assert clean == "Внимание: подробности на в блоге."
    assert "https" not in clean
    assert "*" not in clean


def test_format_terminal_text_escapes_xss_and_renders_links() -> None:
    sample = "Привет! Смотри <script>bad()</script> и [Проект](https://github.com/test)."
    html = str(format_terminal_text(sample))
    assert "&lt;script&gt;" in html
    assert "<script>" not in html
    assert '<a href="https://github.com/test" target="_blank" rel="noopener noreferrer" class="term-link">' in html
    assert "Проект" in html


def test_format_terminal_text_renders_sources_block() -> None:
    sample = (
        "Отличная новость.\n\n"
        "Источники:\n"
        "- [Статья](https://example.com/article)"
    )
    html = str(format_terminal_text(sample))
    assert '<div class="log-sources">' in html
    assert '<div class="sources-tag">// ИСТОЧНИКИ:</div>' in html
    assert '<a href="https://example.com/article"' in html


def test_websocket_streaming_skips_audio_for_sources_section(monkeypatch, tmp_path) -> None:
    synthesized_phrases = []

    async def fake_reply_stream(_, __):
        yield "За август заметно выстрелил проект agency-agents. "
        yield "Он превращает бота в команду.\n\n"
        yield "Источники:\n"
        yield "- [agency-agents](https://github.com/msitarzewski/agency-agents)\n"

    async def fake_synthesize(_, text: str):
        synthesized_phrases.append(text)
        clip = tmp_path / f"clip_{len(synthesized_phrases)}.wav"
        clip.write_bytes(b"RIFF....WAVEfmt ")
        return clip

    monkeypatch.setattr(main_module.CodexAppServer, "reply_stream", fake_reply_stream)
    monkeypatch.setattr(main_module.LocalMacOsSpeaker, "synthesize", fake_synthesize)

    with client.websocket_connect("/ws/conversations/test-sources-conv") as ws:
        ready = ws.receive_json()
        assert ready["type"] == "ready"

        ws.send_json({"type": "text", "text": "Расскажи про репозиторий"})

        transcript = ws.receive_json()
        assert transcript["type"] == "transcript"

        status_thinking = ws.receive_json()
        assert status_thinking["state"] == "thinking"

        all_messages = []
        while True:
            msg = ws.receive_json()
            all_messages.append(msg)
            if msg.get("type") == "status" and msg.get("state") == "idle":
                break

        turn_msg = next(m for m in all_messages if m.get("type") == "turn_completed")
        assert "Источники:" in turn_msg["turn"]["text"]
        assert "https://github.com" in turn_msg["turn"]["text"]

        for phrase in synthesized_phrases:
            assert "Источники" not in phrase
            assert "https://" not in phrase
            assert "github.com" not in phrase

        assert len(synthesized_phrases) >= 1


def test_sanitize_normalizes_quotes_and_removes_backslashes() -> None:
    text = r'Если ты про аниме \*\*«Безупречный мир»\*\*, первый сезон на **24 серии**.'
    clean = sanitize_for_speech(text)
    assert clean == 'Если ты про аниме "Безупречный мир", первый сезон на 24 серии.'
    assert "\\" not in clean
    assert "*" not in clean
    assert "«" not in clean
    assert "»" not in clean


def test_sanitize_normalizes_curly_quotes_and_markdown() -> None:
    text = 'Смотри “Цугаи загробного мира” и `print(1)` ~~старое~~ # Заголовок'
    clean = sanitize_for_speech(text)
    assert clean == 'Смотри "Цугаи загробного мира" и print(1) старое Заголовок'
    assert "“" not in clean
    assert "”" not in clean
    assert "`" not in clean
    assert "~" not in clean
    assert "#" not in clean


def test_format_terminal_text_renders_markdown_formatting() -> None:
    text = 'Аниме **«Безупречный мир»** доступно примерно **284 серии** из *338*, код `python app.py` и ~~черновик~~.'
    html = str(format_terminal_text(text))
    assert "<strong>«Безупречный мир»</strong>" in html
    assert "<strong>284 серии</strong>" in html
    assert "<em>338</em>" in html
    assert "<code>python app.py</code>" in html
    assert "<del>черновик</del>" in html
    assert "**" not in html
    assert "~~" not in html


def test_format_terminal_text_handles_escaped_markdown() -> None:
    text = r'А, понял — \*\*«Цугаи загробного мира»\*\*. Всё готово!'
    html = str(format_terminal_text(text))
    assert "<strong>«Цугаи загробного мира»</strong>" in html
    assert "\\" not in html
    assert "**" not in html


def test_sanitize_strips_trailing_links_without_header() -> None:
    text = (
        "Для оригинальной Switch моя тройка выше остаётся самым надёжным выбором. "
        "[Nintendo Life](https://nintendolife.com), [TechRadar](https://techradar.com)"
    )
    clean = sanitize_for_speech(text)
    assert clean == "Для оригинальной Switch моя тройка выше остаётся самым надёжным выбором."
    assert "Nintendo Life" not in clean
    assert "TechRadar" not in clean
    assert "https" not in clean


def test_sanitize_pure_citation_chunk_returns_empty() -> None:
    chunk = "[Nintendo Life](https://nintendolife.com), [TechRadar](https://techradar.com)"
    assert sanitize_for_speech(chunk) == ""

    bullet_chunk = "- [Nintendo Life](https://nintendolife.com)\n- [TechRadar](https://techradar.com)"
    assert sanitize_for_speech(bullet_chunk) == ""


def test_format_terminal_text_moves_trailing_links_to_sources_box() -> None:
    text = (
        "Для оригинальной Switch моя тройка выше остаётся самым надёжным выбором. "
        "[Nintendo Life](https://nintendolife.com), [TechRadar](https://techradar.com)"
    )
    html = str(format_terminal_text(text))
    assert '<div class="log-sources">' in html
    assert '<div class="sources-tag">// ИСТОЧНИКИ:</div>' in html
    assert '<a href="https://nintendolife.com"' in html
    assert '<a href="https://techradar.com"' in html
    # Check that main text is separated before log-sources
    before, sources = html.split('<div class="log-sources">')
    assert "Nintendo Life" not in before
    assert "TechRadar" not in before
    assert before.strip().endswith("самым надёжным выбором.")


def test_sanitize_strips_slash_sources_section() -> None:
    text = (
        "Если речь о Nintendo Switch, я бы начала с таких игр:\n\n"
        "- Super Mario Bros. Wonder — отличный платформер.\n\n"
        "// источники:\n"
        "- [Nintendo](https://example.com)"
    )
    clean = sanitize_for_speech(text)
    assert "источники" not in clean
    assert "https" not in clean
    assert "Super Mario Bros. Wonder" in clean
    # Bullet marker '-' should be stripped at start of line
    assert not clean.startswith("-")


def test_extract_speech_sentence_handles_markdown_lists_and_abbreviations() -> None:
    from app.main import _extract_speech_sentence

    text = (
        "Если речь о Nintendo Switch и Switch 2 на сегодня, я бы начал с таких игр:\n\n"
        "- The Legend of Zelda: Tears of the Kingdom — огромный открытый мир.\n"
        "- Super Mario Bros. Wonder — один из лучших современных платформеров.\n"
    )

    sentences = []
    buf = ""
    for ch in text:
        buf += ch
        s, buf = _extract_speech_sentence(buf)
        while s:
            sentences.append(s)
            s, buf = _extract_speech_sentence(buf)
    if buf.strip():
        sentences.append(buf.strip())

    assert len(sentences) == 3
    assert sentences[0].startswith("Если речь о Nintendo Switch")
    assert sentences[1].startswith("- The Legend of Zelda")
    assert sentences[2].startswith("- Super Mario Bros. Wonder")
    # Make sure 'Super Mario Bros.' was not falsely cut at 'Bros.'
    assert "Super Mario Bros." not in [s.strip() for s in sentences]


def test_websocket_streaming_speaks_all_bullet_items(monkeypatch, tmp_path) -> None:
    synthesized_phrases = []

    async def fake_reply_stream(_, __):
        yield "Вот отличные игры для Switch:\n\n"
        yield "- The Legend of Zelda: Tears of the Kingdom — огромный мир.\n"
        yield "- Super Mario Bros. Wonder — шикарный платформер.\n"
        yield "- Mario Kart 8 Deluxe — супер для компании.\n\n"
        yield "// источники:\n"
        yield "- [Nintendo](https://example.com)\n"

    async def fake_synthesize(_, text: str):
        synthesized_phrases.append(text)
        clip = tmp_path / f"clip_{len(synthesized_phrases)}.wav"
        clip.write_bytes(b"RIFF....WAVEfmt ")
        return clip

    monkeypatch.setattr(main_module.CodexAppServer, "reply_stream", fake_reply_stream)
    monkeypatch.setattr(main_module.LocalMacOsSpeaker, "synthesize", fake_synthesize)

    with client.websocket_connect("/ws/conversations/test-bullets-conv") as ws:
        ready = ws.receive_json()
        assert ready["type"] == "ready"

        ws.send_json({"type": "text", "text": "Какие игры посоветуешь?"})

        transcript = ws.receive_json()
        assert transcript["type"] == "transcript"

        status_thinking = ws.receive_json()
        assert status_thinking["state"] == "thinking"

        audio_chunks = []
        while True:
            msg = ws.receive_json()
            if msg.get("type") == "audio_chunk":
                audio_chunks.append(msg)
            if msg.get("type") == "status" and msg.get("state") == "idle":
                break

        # All 4 content sentences (intro + 3 bullets) must be synthesized and delivered as audio chunks
        assert len(synthesized_phrases) == 4
        assert len(audio_chunks) == 4
        assert any("Zelda" in p for p in synthesized_phrases)
        assert any("Super Mario Bros" in p for p in synthesized_phrases)
        assert any("Mario Kart" in p for p in synthesized_phrases)
        # Verify inline audio_base64 is sent for direct memory playback
        assert "audio_base64" in audio_chunks[0]
        assert audio_chunks[0]["mime_type"] == "audio/wav"
        import base64
        decoded_clip = base64.b64decode(audio_chunks[0]["audio_base64"])
        assert decoded_clip.startswith(b"RIFF")
        # Sources block must NOT be synthesized
        assert not any("источники" in p.lower() for p in synthesized_phrases)


@pytest.mark.anyio
async def test_speaker_synthesizes_spanish_with_fallback() -> None:
    import shutil
    from app.speak import get_voice_for_locale

    # Verify helper finds Spanish voice if installed
    es_voice = get_voice_for_locale("es")
    if es_voice and shutil.which("say") is not None:
        speaker = LocalMacOsSpeaker(voice="Milena")
        wav = await speaker.synthesize("¡Hola, qué tal! ¿Cómo estás hoy?")
        assert wav is not None
        assert wav.is_file()
        wav.unlink(missing_ok=True)


def test_extract_speech_sentence_early_first_clause() -> None:
    from app.main import _extract_speech_sentence

    # 1. Early clause splitting on first chunk (comma with >= 3 words and >= 12 chars)
    buf = "Конечно, мы можем это сделать, если посмотрим в настройки."
    s, rem = _extract_speech_sentence(buf, is_first_chunk=True)
    assert s == "Конечно, мы можем это сделать,"
    assert rem == "если посмотрим в настройки."

    # 2. Too short introductory word (< 3 words) does not trigger premature comma split
    short_buf = "Да, конечно мы проверим этот вариант позже."
    s_short, rem_short = _extract_speech_sentence(short_buf, is_first_chunk=True)
    assert s_short is None
    assert rem_short == short_buf

    # 3. Subsequent chunk (is_first_chunk=False) does NOT split on comma
    s_sub, rem_sub = _extract_speech_sentence(buf, is_first_chunk=False)
    assert s_sub is None
    assert rem_sub == buf

    # 4. First chunk with complete sentence splits immediately
    full_buf = "Привет! Как твои дела сегодня?"
    s_full, rem_full = _extract_speech_sentence(full_buf, is_first_chunk=True)
    assert s_full == "Привет!"
    assert rem_full == "Как твои дела сегодня?"


def test_default_base_instructions_latency_directive() -> None:
    from app.codex import DEFAULT_BASE_INSTRUCTIONS

    assert "opening phrase or clause" in DEFAULT_BASE_INSTRUCTIONS
    assert "without delay" in DEFAULT_BASE_INSTRUCTIONS


def test_get_installed_voices_includes_edge_voices() -> None:
    from app.speak import get_installed_voices

    voices = get_installed_voices(force_refresh=True)
    edge_voices = [v for v in voices if v.engine == "edge"]
    assert len(edge_voices) >= 2
    names = [v.name for v in edge_voices]
    assert "Svetlana (Neural · Edge)" in names
    assert "Dmitry (Neural · Edge)" in names
    assert all(v.is_russian for v in edge_voices)


@pytest.mark.anyio
async def test_speaker_synthesizes_edge_tts_mp3(monkeypatch, tmp_path) -> None:
    fake_mp3 = tmp_path / "fake_edge.mp3"
    fake_mp3.write_bytes(b"ID3fake_mp3_data")

    async def fake_save(self, path):
        Path(path).write_bytes(fake_mp3.read_bytes())

    class FakeCommunicate:
        def __init__(self, text, voice, rate="+5%"):
            self.text = text
            self.voice = voice

        async def save(self, path):
            await fake_save(self, path)

    import edge_tts
    monkeypatch.setattr(edge_tts, "Communicate", FakeCommunicate)

    speaker = LocalMacOsSpeaker(voice="Svetlana (Neural · Edge)")
    result_path = await speaker.synthesize("Привет, это тест Светланы!")
    assert result_path is not None
    assert result_path.is_file()
    assert result_path.suffix == ".mp3"
    assert result_path.read_bytes() == b"ID3fake_mp3_data"
    result_path.unlink(missing_ok=True)


@pytest.mark.anyio
async def test_speaker_edge_tts_fallback_to_macos_on_error(monkeypatch, tmp_path) -> None:
    # Simulate network error in Edge TTS
    class FailingCommunicate:
        def __init__(self, text, voice, rate="+5%"):
            pass

        async def save(self, path):
            raise ConnectionError("Microsoft Edge TTS service unreachable")

    import edge_tts
    monkeypatch.setattr(edge_tts, "Communicate", FailingCommunicate)

    # Mock macos say
    fake_wav = tmp_path / "fallback_milena.wav"
    fake_wav.write_bytes(b"RIFFfallback_wav")

    async def fake_macos(clean_text, active_voice):
        dest = tmp_path / "called_macos.wav"
        dest.write_bytes(fake_wav.read_bytes())
        return dest

    speaker = LocalMacOsSpeaker(voice="Svetlana (Neural · Edge)")
    monkeypatch.setattr(speaker, "_synthesize_macos", fake_macos)

    result_path = await speaker.synthesize("Тест автоматического отката на локальный синтез.")
    assert result_path is not None
    assert result_path.read_bytes() == b"RIFFfallback_wav"
    result_path.unlink(missing_ok=True)


def test_get_speech_serves_mp3_media_type(tmp_path) -> None:
    from app import main as main_module

    fake_mp3 = tmp_path / "clip.mp3"
    fake_mp3.write_bytes(b"ID3data")
    clip_id = "test-mp3-clip"
    main_module.speech_clips[clip_id] = main_module.SpeechClip(
        conversation_id="any-conv", path=fake_mp3
    )

    resp = client.get(f"/speech/{clip_id}")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("audio/mpeg")


def test_get_installed_voices_includes_silero_voices() -> None:
    from app.speak import get_installed_voices

    voices = get_installed_voices(force_refresh=True)
    silero_voices = [v for v in voices if v.engine == "silero"]
    assert len(silero_voices) == 3
    names = [v.name for v in silero_voices]
    assert "Ksenia (Silero Neural · Offline)" in names
    assert "Baya (Silero Neural · Offline)" in names
    assert "Aidar (Silero Neural · Offline)" in names
    assert all(v.is_russian for v in silero_voices)


@pytest.mark.anyio
async def test_speaker_synthesizes_silero_wav(monkeypatch, tmp_path) -> None:
    fake_wav = tmp_path / "fake_silero.wav"
    fake_wav.write_bytes(b"RIFFsilero_test_wav")

    async def fake_silero(self, clean_text, voice_name):
        dest = tmp_path / "output_silero.wav"
        dest.write_bytes(fake_wav.read_bytes())
        return dest

    monkeypatch.setattr(LocalMacOsSpeaker, "_synthesize_silero", fake_silero)

    speaker = LocalMacOsSpeaker(voice="Ksenia (Silero Neural · Offline)")
    result_path = await speaker.synthesize("Привет, это тест Ксении!")
    assert result_path is not None
    assert result_path.is_file()
    assert result_path.read_bytes() == b"RIFFsilero_test_wav"
    result_path.unlink(missing_ok=True)


@pytest.mark.anyio
async def test_prewarm_voice_loads_only_an_installed_silero_model(monkeypatch, tmp_path) -> None:
    from app import speak

    model_path = tmp_path / ".cache" / "voice-of-luna" / "models" / "silero_v4_ru.pt"
    model_path.parent.mkdir(parents=True)
    model_path.write_bytes(b"model")
    loaded = []

    monkeypatch.setattr(speak.Path, "home", classmethod(lambda _: tmp_path))
    monkeypatch.setattr(speak, "_get_silero_model", lambda: loaded.append(True))

    await speak.prewarm_voice("Ksenia (Silero Neural · Offline)")
    assert loaded == [True]

    loaded.clear()
    await speak.prewarm_voice("Milena")
    assert loaded == []


@pytest.mark.anyio
async def test_speaker_silero_fallback_to_macos_on_error(monkeypatch, tmp_path) -> None:
    async def failing_silero(self, clean_text, voice_name):
        raise RuntimeError("Silero inference crash")

    monkeypatch.setattr(LocalMacOsSpeaker, "_synthesize_silero", failing_silero)

    fake_wav = tmp_path / "fallback_milena.wav"
    fake_wav.write_bytes(b"RIFFfallback_from_silero")

    async def fake_macos(self, clean_text, active_voice):
        dest = tmp_path / "called_macos.wav"
        dest.write_bytes(fake_wav.read_bytes())
        return dest

    monkeypatch.setattr(LocalMacOsSpeaker, "_synthesize_macos", fake_macos)

    speaker = LocalMacOsSpeaker(voice="Baya (Silero Neural · Offline)")
    result_path = await speaker.synthesize("Тест падения Silero.")
    assert result_path is not None
    assert result_path.read_bytes() == b"RIFFfallback_from_silero"
    result_path.unlink(missing_ok=True)


def test_transliterate_latin_for_speech() -> None:
    from app.speak import transliterate_latin_for_speech

    # Known dictionary terms
    sample = "The Legend of Zelda: Tears of the Kingdom на Nintendo Switch"
    trans = transliterate_latin_for_speech(sample)
    assert "The" not in trans
    assert "Zelda" not in trans
    assert "Switch" not in trans
    assert "Зе Ледженд оф Зельда: Тирс оф зе Кингдом на Нинтендо Свитч" in trans

    # General / rule-based transliteration
    sample2 = "Запусти docker контейнер и проверь FastAPI app на Python"
    trans2 = transliterate_latin_for_speech(sample2)
    assert "docker" not in trans2
    assert "FastAPI" not in trans2
    assert "Python" not in trans2
    assert "докер" in trans2
    assert "Фастапи" in trans2 or "фастапи" in trans2.lower()
    assert "пайтон" in trans2.lower()

    # Plain Russian text without Latin words should remain unchanged
    plain = "Привет, как твои дела? Всё отлично!"
    assert transliterate_latin_for_speech(plain) == plain


@pytest.mark.anyio
async def test_silero_synthesize_transliterates_latin(monkeypatch, tmp_path) -> None:
    received_texts = []

    class FakeSileroModel:
        def apply_tts(self, text, speaker, sample_rate):
            received_texts.append(text)
            import torch
            return torch.zeros(100, dtype=torch.float32)

    monkeypatch.setattr("app.speak._get_silero_model", lambda: FakeSileroModel())

    speaker = LocalMacOsSpeaker(voice="Ksenia (Silero Neural · Offline)")
    clip = await speaker._synthesize_silero(
        "The Legend of Zelda на Nintendo Switch отличная игра!",
        "Ksenia (Silero Neural · Offline)",
    )
    assert clip.is_file()
    clip.unlink(missing_ok=True)

    assert len(received_texts) == 1
    passed_text = received_texts[0]
    # Verify no Latin letters remained in the string passed to Silero
    import re
    assert not re.search(r"[A-Za-z]", passed_text)
    assert "Зельда" in passed_text or "зельда" in passed_text.lower()
    assert "Свитч" in passed_text or "свитч" in passed_text.lower()


def test_websocket_streaming_voices_all_sentences_with_links_in_bullets(monkeypatch, tmp_path) -> None:
    synthesized_phrases = []

    async def fake_reply_stream(_, __):
        yield "Конечно, вот отличный старт:\n\n"
        yield "- [The Legend of Zelda](https://nintendo.com) — огромный открытый мир.\n"
        yield "- [Super Mario Bros. Wonder](https://nintendo.com) — отличный платформер.\n\n"
        yield "Источники:\n"
        yield "- [Nintendo](https://example.com)\n"

    async def fake_synthesize(_, text: str):
        synthesized_phrases.append(text)
        clip = tmp_path / f"clip_{len(synthesized_phrases)}.mp3"
        clip.write_bytes(b"ID3fake_mp3")
        return clip

    monkeypatch.setattr(main_module.CodexAppServer, "reply_stream", fake_reply_stream)
    monkeypatch.setattr(main_module.LocalMacOsSpeaker, "synthesize", fake_synthesize)

    with client.websocket_connect("/ws/conversations/test-multisentence-links") as ws:
        ready = ws.receive_json()
        assert ready["type"] == "ready"

        ws.send_json({"type": "text", "text": "Посоветуй игры"})

        transcript = ws.receive_json()
        assert transcript["type"] == "transcript"

        status_thinking = ws.receive_json()
        assert status_thinking["state"] == "thinking"

        audio_chunks = []
        while True:
            msg = ws.receive_json()
            if msg.get("type") == "audio_chunk":
                audio_chunks.append(msg)
            if msg.get("type") == "status" and msg.get("state") == "idle":
                break

        # Intro + 2 bullet items must all be synthesized (3 total chunks)
        assert len(synthesized_phrases) == 3
        assert len(audio_chunks) == 3
        assert any("отличный старт" in p for p in synthesized_phrases)
        assert any("The Legend of Zelda" in p for p in synthesized_phrases)
        assert any("Super Mario Bros" in p for p in synthesized_phrases)
        # Verify correct audio/mpeg mime_type for mp3 clips
        assert audio_chunks[0]["mime_type"] == "audio/mpeg"
        # Explicit sources section must be skipped
        assert not any("Источники" in p for p in synthesized_phrases)







