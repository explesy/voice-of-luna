"""Deterministic mixed-language speech segmentation and routing (issue #13).

Every test here is local and offline: no Codex, Edge, network, model download or
paid call. The router itself is pure; the installed-voice lookups are replaced
with fixed inventories where voice selection matters.
"""

from __future__ import annotations

import shutil
import wave
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import main as main_module
from app.main import app
from app.plugins import Plugin, plugin_manager
from app.speech_pipeline import (
    AMBIGUOUS_LATIN_TOKENS,
    ENGLISH_SPEECH_CUES,
    SPANISH_SPEECH_CUES,
    ROLE_EMBEDDED,
    ROLE_EXPLANATION,
    ROLE_PRIMARY,
    VoicePlan,
    join_speech_clips,
    route_speech,
    segment_language_runs,
)
from app.speak import VoiceInfo, get_ready_voice_for_locale

client = TestClient(app)

AC_PHRASE = "включи Docker container и проверь build"


# --- pure segmentation --------------------------------------------------------


def test_segments_russian_with_embedded_english() -> None:
    runs = segment_language_runs(AC_PHRASE, "ru-RU")
    assert [(run.language, run.role) for run in runs] == [
        ("ru", ROLE_PRIMARY),
        ("en", ROLE_EMBEDDED),
        ("ru", ROLE_PRIMARY),
        ("en", ROLE_EMBEDDED),
    ]
    assert [run.text.strip() for run in runs] == ["включи", "Docker container", "и проверь", "build"]


def test_segmentation_is_deterministic_and_preserves_content() -> None:
    first = segment_language_runs(AC_PHRASE, "ru-RU")
    second = segment_language_runs(AC_PHRASE, "ru-RU")
    assert first == second
    rebuilt = "".join(run.text for run in first).split()
    assert rebuilt == AC_PHRASE.split()


def test_single_language_text_stays_one_run() -> None:
    runs = segment_language_runs("Привет, я твой голосовой помощник.", "ru-RU")
    assert len(runs) == 1
    assert runs[0].language == "ru"
    assert runs[0].role == ROLE_PRIMARY


def test_ambiguous_short_tokens_do_not_flap() -> None:
    # "no", "ok", "in", "la" carry no evidence and must inherit the Russian run.
    runs = segment_language_runs("да, no problem, ok", "ru-RU")
    assert len(runs) == 1
    assert runs[0].language == "ru"
    # Accented ambiguous tokens are ambiguous too: "sí" must not switch on its own.
    accented = segment_language_runs("да, sí, продолжим", "ru-RU")
    assert len(accented) == 1
    assert accented[0].language == "ru"


def test_punctuation_joined_foreign_word_is_not_swallowed() -> None:
    runs = segment_language_runs("Привет,Docker container", "ru-RU")
    assert [(run.language, run.text.strip()) for run in runs] == [
        ("ru", "Привет,"),
        ("en", "Docker container"),
    ]


def test_standalone_technical_token_routes_english() -> None:
    runs = segment_language_runs("проверь build", "ru-RU")
    assert [(run.language, run.text.strip()) for run in runs] == [("ru", "проверь"), ("en", "build")]


def test_spanish_accent_and_cue_evidence() -> None:
    accented = segment_language_runs("¿Cómo estás hoy?", "en-US")
    assert accented[0].language == "es"
    cues = segment_language_runs("Hola, vamos a practicar", "en-US")
    assert all(run.language == "es" for run in cues)


def test_english_primary_keeps_plain_latin() -> None:
    runs = segment_language_runs("Please open the project folder", "en-US")
    assert len(runs) == 1
    assert runs[0].language == "en"


def test_unknown_script_inherits_neighbour() -> None:
    runs = segment_language_runs("привет 你好 мир", "ru-RU")
    assert len(runs) == 1
    assert runs[0].language == "ru"


def test_cue_lists_are_disjoint_and_do_not_overlap_ambiguous() -> None:
    assert not (ENGLISH_SPEECH_CUES & SPANISH_SPEECH_CUES)
    assert not (AMBIGUOUS_LATIN_TOKENS & ENGLISH_SPEECH_CUES)
    assert not (AMBIGUOUS_LATIN_TOKENS & SPANISH_SPEECH_CUES)


# --- routing ------------------------------------------------------------------


def _plan(**overrides) -> VoicePlan:
    values = {
        "primary_locale": "ru-RU",
        "primary_voice": "Dmitri (Piper Neural · Offline)",
        "explanation_locale": None,
        "explanation_voice": None,
        "enabled": True,
    }
    values.update(overrides)
    return VoicePlan(**values)


def test_route_speech_uses_english_voice_for_embedded_runs() -> None:
    chunks = route_speech(AC_PHRASE, _plan(), resolve_voice=lambda lang: "Lessac" if lang == "en" else None)
    assert [chunk.voice for chunk in chunks] == [
        "Dmitri (Piper Neural · Offline)",
        "Lessac",
        "Dmitri (Piper Neural · Offline)",
        "Lessac",
    ]
    assert "Docker container" in "".join(chunk.text for chunk in chunks)


def test_route_speech_explanation_locale_uses_distinct_voice() -> None:
    plan = _plan(
        primary_locale="es-ES",
        primary_voice="Elvira (Neural · Edge)",
        explanation_locale="ru-RU",
        explanation_voice="Dmitri (Piper Neural · Offline)",
    )
    chunks = route_speech("Hola, vamos a practicar. Здесь нужен субхунтив.", plan)
    assert [chunk.role for chunk in chunks] == [ROLE_PRIMARY, ROLE_EXPLANATION]
    assert chunks[1].voice == "Dmitri (Piper Neural · Offline)"
    assert chunks[1].voice != plan.primary_voice


def test_route_speech_disabled_is_single_voice() -> None:
    chunks = route_speech(AC_PHRASE, _plan(enabled=False), resolve_voice=lambda lang: "Lessac")
    assert len(chunks) == 1
    assert chunks[0].voice == "Dmitri (Piper Neural · Offline)"
    assert chunks[0].text == AC_PHRASE


def test_route_speech_missing_voice_degrades_to_primary() -> None:
    chunks = route_speech(AC_PHRASE, _plan(), resolve_voice=lambda lang: None)
    assert len(chunks) == 1
    assert chunks[0].voice == "Dmitri (Piper Neural · Offline)"
    # The text is unchanged, so the existing single-voice fallback still applies.
    assert "Docker" in chunks[0].text


def test_route_speech_explanation_without_distinct_voice_degrades() -> None:
    plan = _plan(
        primary_locale="es-ES",
        primary_voice="Elvira (Neural · Edge)",
        explanation_locale="ru-RU",
        explanation_voice=None,
    )
    chunks = route_speech("Hola. Здесь объяснение.", plan)
    assert all(chunk.voice == plan.primary_voice for chunk in chunks)


def test_route_speech_empty_text() -> None:
    assert route_speech("   ", _plan()) == []


# --- installed-voice resolution ----------------------------------------------


def _voice(name: str, locale: str, engine: str, *, downloaded: bool = True) -> VoiceInfo:
    return VoiceInfo(name=name, locale=locale, sample="", engine=engine, is_downloaded=downloaded)


def test_get_ready_voice_for_locale_skips_missing_models(monkeypatch) -> None:
    inventory = [
        _voice("Lessac (Piper Neural · Offline)", "en_US", "piper", downloaded=False),
        _voice("Samantha", "en_US", "macos"),
        _voice("Jenny (Neural · Edge)", "en_US", "edge"),
    ]
    monkeypatch.setattr("app.speak.get_installed_voices", lambda force_refresh=False: inventory)
    assert get_ready_voice_for_locale("en") == "Samantha"


def test_get_ready_voice_for_locale_prefers_piper_when_installed(monkeypatch) -> None:
    inventory = [
        _voice("Lessac (Piper Neural · Offline)", "en_US", "piper"),
        _voice("Samantha", "en_US", "macos"),
    ]
    monkeypatch.setattr("app.speak.get_installed_voices", lambda force_refresh=False: inventory)
    assert get_ready_voice_for_locale("en") == "Lessac (Piper Neural · Offline)"


def test_get_ready_voice_for_locale_excludes_voice(monkeypatch) -> None:
    inventory = [
        _voice("Samantha", "en_US", "macos"),
        _voice("Jenny (Neural · Edge)", "en_US", "edge"),
    ]
    monkeypatch.setattr("app.speak.get_installed_voices", lambda force_refresh=False: inventory)
    assert get_ready_voice_for_locale("en", exclude_voice="Samantha") == "Jenny (Neural · Edge)"


def test_get_ready_voice_for_locale_returns_none_without_match(monkeypatch) -> None:
    monkeypatch.setattr("app.speak.get_installed_voices", lambda force_refresh=False: [])
    assert get_ready_voice_for_locale("en") is None


# --- plugin-declared explanation locale --------------------------------------


class ExplanationLocalePlugin(Plugin):
    id = "explanation_locale_test"
    name = "Explanation locale test"
    response_locale_override = "es-ES"
    explanation_locale = "ru-RU"


def test_plugin_explanation_locale_resolves_distinct_voice(monkeypatch) -> None:
    from app.conversation_service import Conversation, resolve_turn_language

    plugin_manager.register(ExplanationLocalePlugin())
    inventory = [
        _voice("Elvira (Neural · Edge)", "es_ES", "edge"),
        _voice("Dmitri (Piper Neural · Offline)", "ru_RU", "piper"),
    ]
    monkeypatch.setattr("app.speak.get_installed_voices", lambda force_refresh=False: inventory)

    conversation = Conversation(id="explanation-locale", plugin_id=ExplanationLocalePlugin.id)
    language = resolve_turn_language(conversation, user_text="Hola, vamos a practicar")

    assert language.explanation_locale == "ru-RU"
    assert language.explanation_voice == "Dmitri (Piper Neural · Offline)"
    assert language.explanation_voice != language.speaker_voice
    assert plugin_manager.get_explanation_locale("neutral") is None


# --- plan resolution ----------------------------------------------------------


def test_resolve_voice_plan_disabled_by_env(monkeypatch) -> None:
    from app.conversation_service import Conversation, TurnLanguage, resolve_voice_plan

    monkeypatch.setenv("VOICE_OF_LUNA_SEGMENT_ROUTING", "0")
    language = TurnLanguage("ru-RU", "ru-RU", "ru-RU", "Dmitri")
    plan = resolve_voice_plan(Conversation(id="env-off"), language)
    assert plan.enabled is False


def test_resolve_voice_plan_native_voice_bypasses_routing(monkeypatch) -> None:
    from app import conversation_service
    from app import model_catalog
    from app.conversation_service import Conversation, TurnLanguage, resolve_voice_plan

    monkeypatch.delenv("VOICE_OF_LUNA_SEGMENT_ROUTING", raising=False)
    monkeypatch.setattr(
        model_catalog,
        "voice_capability",
        lambda name, locale="", **kwargs: model_catalog.VoiceCapability(
            ("en", "ru"), True, model_catalog.CODE_SWITCHING_NATIVE
        ),
    )
    language = TurnLanguage("en-US", "en-US", "en-US", "Ava Multilingual")
    plan = resolve_voice_plan(Conversation(id="native"), language)
    assert plan.enabled is False


def test_resolve_voice_plan_explanation_keeps_routing_for_native_voice(monkeypatch) -> None:
    from app import model_catalog
    from app.conversation_service import Conversation, TurnLanguage, resolve_voice_plan

    monkeypatch.delenv("VOICE_OF_LUNA_SEGMENT_ROUTING", raising=False)
    monkeypatch.setattr(
        model_catalog,
        "voice_capability",
        lambda name, locale="", **kwargs: model_catalog.VoiceCapability(
            ("en", "ru"), True, model_catalog.CODE_SWITCHING_NATIVE
        ),
    )
    language = TurnLanguage("en-US", "en-US", "en-US", "Ava Multilingual", "ru-RU", "Dmitri")
    plan = resolve_voice_plan(Conversation(id="native-explanation"), language)
    assert plan.enabled is True
    assert plan.explanation_voice == "Dmitri"


# --- streaming integration ----------------------------------------------------


def _write_wav(path: Path, frames: bytes = b"\x00\x00" * 2400) -> Path:
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(24000)
        handle.writeframes(frames)
    return path


def test_websocket_mixed_language_turn_routes_per_run(monkeypatch, tmp_path) -> None:
    synth_calls: list[tuple[str, str | None]] = []

    async def fake_reply_stream(_, __):
        yield "Включи Docker container и проверь build."

    async def fake_synthesize_with_metadata(self, text, voice=None):
        synth_calls.append((text, voice))
        clip = tmp_path / f"mixed_{len(synth_calls)}.wav"
        clip.write_bytes(b"RIFF....WAVEfmt ")
        return main_module.SpeechSynthesisResult(
            path=clip, requested_engine="piper", actual_engine="piper", actual_voice=voice or self.voice
        )

    monkeypatch.setattr(main_module.CodexAppServer, "reply_stream", fake_reply_stream)
    monkeypatch.setattr(main_module.LocalMacOsSpeaker, "synthesize_with_metadata", fake_synthesize_with_metadata)
    monkeypatch.setattr(main_module, "get_ready_voice_for_locale", lambda language, exclude_voice=None: f"{language}-voice")

    with client.websocket_connect("/ws/conversations/test-mixed-routing") as ws:
        assert ws.receive_json()["type"] == "ready"
        ws.send_json({"type": "text", "text": "Сделай это"})
        assert ws.receive_json()["type"] == "transcript"
        assert ws.receive_json()["state"] == "thinking"

        audio_chunk_texts: list[str] = []
        while True:
            msg = ws.receive_json()
            if msg.get("type") == "audio_chunk":
                audio_chunk_texts.append(msg.get("text") or "")
            if msg.get("type") == "status" and msg.get("state") == "idle":
                break

    texts = [text for text, _ in synth_calls]
    assert "Docker container" in texts
    assert any(voice == "en-voice" for _, voice in synth_calls)
    assert any(voice != "en-voice" for _, voice in synth_calls)
    assert len({voice for _, voice in synth_calls}) >= 2
    # Every synthesized run was delivered, in order.
    assert audio_chunk_texts == texts


# --- local join for single-clip endpoints ------------------------------------


def test_join_speech_clips_concatenates_local_wavs(tmp_path) -> None:
    if shutil.which("ffmpeg") is None:
        pytest.skip("ffmpeg is required for the local join")
    import asyncio

    first = _write_wav(tmp_path / "a.wav")
    second = _write_wav(tmp_path / "b.wav", b"\x10\x00" * 1200)
    destination = tmp_path / "joined.wav"
    assert asyncio.run(join_speech_clips([first, second], destination)) is True
    assert destination.is_file() and destination.stat().st_size > 0
    with wave.open(str(destination), "rb") as handle:
        assert handle.getframerate() == 24000
        assert handle.getnchannels() == 1
        assert handle.getnframes() > 2400


def test_join_speech_clips_rejects_empty(tmp_path) -> None:
    import asyncio

    assert asyncio.run(join_speech_clips([], tmp_path / "none.wav")) is False


def test_single_clip_routing_sanitizes_sources_before_segmenting(monkeypatch, tmp_path) -> None:
    import asyncio

    calls: list[str] = []

    async def fake_synthesize_with_metadata(self, text, voice=None):
        calls.append(text)
        clip = tmp_path / f"sanitized_{len(calls)}.wav"
        _write_wav(clip, b"RIFF" + b"\x00" * 40)
        return main_module.SpeechSynthesisResult(
            path=clip, requested_engine="piper", actual_engine="piper", actual_voice=voice or self.voice
        )

    monkeypatch.setattr(main_module.LocalMacOsSpeaker, "synthesize_with_metadata", fake_synthesize_with_metadata)
    monkeypatch.setattr(main_module, "get_ready_voice_for_locale", lambda language, exclude_voice=None: "en-voice")

    speaker = main_module.LocalMacOsSpeaker(voice="Dmitri (Piper Neural · Offline)")
    plan = VoicePlan("ru-RU", "Dmitri (Piper Neural · Offline)", enabled=True)
    result = asyncio.run(
        main_module._synthesize_routed_single_clip(speaker, "Ответ готов.\nSources:\nDocker documentation", plan)
    )
    assert result is not None
    result.path.unlink(missing_ok=True)
    # The sources block is stripped before segmentation, so its English content
    # is never handed to any voice.
    assert all("Docker" not in call and "Sources" not in call for call in calls)
    assert calls == ["Ответ готов."]


def test_piper_cancellation_does_not_leave_a_clip(monkeypatch) -> None:
    import asyncio
    import threading

    from app import speak as speak_module

    created: list[Path] = []
    real_mkstemp = speak_module.tempfile.mkstemp

    def recording_mkstemp(*args, **kwargs):
        descriptor, raw = real_mkstemp(*args, **kwargs)
        created.append(Path(raw))
        return descriptor, raw

    monkeypatch.setattr(speak_module.tempfile, "mkstemp", recording_mkstemp)
    monkeypatch.setattr(speak_module, "resolve_piper_model", lambda voice: "test_model")
    monkeypatch.setattr(speak_module, "_piper_model_languages", lambda key: ("ru",))

    worker_started = threading.Event()
    release = threading.Event()

    class FakeVoice:
        def synthesize_wav(self, text, wav_file):
            worker_started.set()
            release.wait(timeout=5)
            wav_file.setnchannels(1)
            wav_file.setsampwidth(2)
            wav_file.setframerate(22050)
            wav_file.writeframes(b"\x00\x00" * 100)

    monkeypatch.setattr(speak_module, "_get_piper_voice", lambda key: FakeVoice())
    speaker = speak_module.LocalMacOsSpeaker(voice="Dmitri (Piper Neural · Offline)")

    async def scenario() -> None:
        task = asyncio.create_task(speaker._synthesize_piper("привет", "Dmitri (Piper Neural · Offline)"))
        while not worker_started.is_set():
            await asyncio.sleep(0.01)
        task.cancel()
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        # The worker may still be finishing in its thread; poll briefly.
        for _ in range(100):
            if not any(path.exists() for path in created):
                break
            await asyncio.sleep(0.02)

    asyncio.run(scenario())
    assert created, "test did not observe a temporary clip path"
    assert not any(path.exists() for path in created)


def test_gated_turn_routes_mixed_text_after_approval(monkeypatch, tmp_path) -> None:
    from app.plugins import ResponseDecision

    class ApprovingGatedPlugin(Plugin):
        id = "gated_routing_test"
        name = "Gated routing test"
        delivery_mode = "gated"

        async def validate_response(self, ctx, candidate):
            return ResponseDecision(action="allow", text=candidate.text, reason="test")

    plugin_manager.register(ApprovingGatedPlugin())
    synth_calls: list[tuple[str, str | None]] = []

    async def fake_call_reply(*args, **kwargs):
        return AC_PHRASE

    async def fake_synthesize_with_metadata(self, text, voice=None):
        synth_calls.append((text, voice))
        clip = tmp_path / f"gated_{len(synth_calls)}.wav"
        clip.write_bytes(b"RIFF....WAVEfmt ")
        return main_module.SpeechSynthesisResult(
            path=clip, requested_engine="piper", actual_engine="piper", actual_voice=voice or self.voice
        )

    monkeypatch.setattr(main_module, "_call_reply", fake_call_reply)
    monkeypatch.setattr(main_module.LocalMacOsSpeaker, "synthesize_with_metadata", fake_synthesize_with_metadata)
    monkeypatch.setattr(main_module, "get_ready_voice_for_locale", lambda language, exclude_voice=None: f"{language}-voice")

    conversation_id = client.post("/api/conversations").json()["id"]
    selected = client.post(
        f"/api/conversations/{conversation_id}/plugin", json={"plugin_id": "gated_routing_test"}
    )
    assert selected.status_code == 200

    response = client.post(f"/api/conversations/{conversation_id}/turns", json={"text": "Сделай это"})
    assert response.status_code == 200
    assert response.json()["text"] == AC_PHRASE

    assert any(voice == "en-voice" for _, voice in synth_calls)
    assert any(voice != "en-voice" for _, voice in synth_calls)
    client.delete(f"/api/conversations/{conversation_id}")


def test_resynthesize_replay_routes_mixed_text_into_one_clip(monkeypatch, tmp_path) -> None:
    synth_calls: list[tuple[str, str | None, Path]] = []

    async def fake_synthesize_with_metadata(self, text, voice=None):
        clip = tmp_path / f"replay_{len(synth_calls)}.wav"
        _write_wav(clip, b"RIFF" + b"\x00" * 40)
        synth_calls.append((text, voice, clip))
        return main_module.SpeechSynthesisResult(
            path=clip,
            requested_engine="piper",
            actual_engine="piper",
            actual_voice=voice or self.voice,
        )

    async def fake_join(clips, destination):
        destination.write_bytes(b"RIFFjoined")
        return True

    monkeypatch.setattr(main_module.LocalMacOsSpeaker, "synthesize_with_metadata", fake_synthesize_with_metadata)
    monkeypatch.setattr(main_module, "join_speech_clips", fake_join)
    monkeypatch.setattr(main_module, "get_ready_voice_for_locale", lambda language, exclude_voice=None: "en-voice")

    conversation_id = client.post("/api/conversations").json()["id"]
    conversation = main_module.conversations[conversation_id]
    conversation.turns.append({"role": "assistant", "text": AC_PHRASE, "turn_id": "mixed-1"})

    response = client.post(
        f"/api/conversations/{conversation_id}/turns/0/resynthesize",
        json={"turn_id": "mixed-1"},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["text"] == AC_PHRASE
    assert data["replay"] is True
    assert data["fallback"] is False
    assert any(voice == "en-voice" for _, voice, _ in synth_calls)
    assert any(voice != "en-voice" for _, voice, _ in synth_calls)
    assert data["audio_base64"]
    # Every intermediate run clip and the served joined clip are cleaned up.
    assert all(not clip.exists() for _, _, clip in synth_calls)
    client.delete(f"/api/conversations/{conversation_id}")


class BilingualReplayPlugin(Plugin):
    id = "bilingual_replay_test"
    name = "Bilingual replay test"
    response_locale_override = "es-ES"
    explanation_locale = "ru-RU"


def test_plain_replay_keeps_target_and_explanation_voices(monkeypatch, tmp_path) -> None:
    plugin_manager.register(BilingualReplayPlugin())
    inventory = [
        _voice("Elvira (Neural · Edge)", "es_ES", "edge"),
        _voice("Dmitri (Piper Neural · Offline)", "ru_RU", "piper"),
    ]
    monkeypatch.setattr("app.speak.get_installed_voices", lambda force_refresh=False: inventory)

    synth_calls: list[tuple[str, str | None]] = []

    async def fake_synthesize_with_metadata(self, text, voice=None):
        clip = tmp_path / f"bilingual_{len(synth_calls)}.wav"
        _write_wav(clip, b"RIFF" + b"\x00" * 40)
        synth_calls.append((text, voice))
        return main_module.SpeechSynthesisResult(
            path=clip, requested_engine="edge", actual_engine="edge", actual_voice=voice or self.voice
        )

    async def fake_join(clips, destination):
        destination.write_bytes(b"RIFFjoined")
        return True

    monkeypatch.setattr(main_module.LocalMacOsSpeaker, "synthesize_with_metadata", fake_synthesize_with_metadata)
    monkeypatch.setattr(main_module, "join_speech_clips", fake_join)

    conversation_id = client.post("/api/conversations").json()["id"]
    selected = client.post(
        f"/api/conversations/{conversation_id}/plugin", json={"plugin_id": BilingualReplayPlugin.id}
    )
    assert selected.status_code == 200
    conversation = main_module.conversations[conversation_id]
    conversation.turns.append(
        {"role": "assistant", "text": "Hola, vamos a practicar. Здесь нужен субхунтив.", "turn_id": "bi-1"}
    )

    response = client.post(
        f"/api/conversations/{conversation_id}/turns/0/resynthesize", json={"turn_id": "bi-1"}
    )
    assert response.status_code == 200
    voices = {voice for _, voice in synth_calls}
    assert "Elvira (Neural · Edge)" in voices
    assert "Dmitri (Piper Neural · Offline)" in voices
    # A plain replay must not collapse both runs onto the session's primary voice.
    assert len(voices) >= 2
    client.delete(f"/api/conversations/{conversation_id}")
