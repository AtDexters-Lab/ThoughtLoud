import base64
import io
import wave

import pytest
import requests


def _write_silence(path, *, duration_seconds: float, frame_rate: int = 100):
    frames = int(duration_seconds * frame_rate)
    with wave.open(str(path), "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(frame_rate)
        output.writeframes(b"\x00\x00" * frames)


def _silence_wav_bytes(*, duration_seconds: float, frame_rate: int = 100):
    output = io.BytesIO()
    frames = int(duration_seconds * frame_rate)
    with wave.open(output, "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(frame_rate)
        audio.writeframes(b"\x00\x00" * frames)
    return output.getvalue()


class _Response:
    def __init__(self, content, *, model=None, system_fingerprint=None):
        self._content = content
        self._model = model
        self._system_fingerprint = system_fingerprint

    def raise_for_status(self):
        return None

    def json(self):
        return {
            "choices": [{"message": {"content": self._content}}],
            "model": self._model,
            "system_fingerprint": self._system_fingerprint,
        }


class _Session:
    def __init__(self, responses):
        self.responses = iter(responses)
        self.calls = []

    def post(self, url, *, json, timeout):
        self.calls.append((url, json, timeout))
        response = next(self.responses)
        if isinstance(response, _Response):
            return response
        return _Response(response)


def test_saved_wav_uses_the_shared_segmenter_and_direct_assembly(tmp_path):
    from voxd.core.gemma_transcriber import GemmaAudioTranscriber

    audio = tmp_path / "long.wav"
    _write_silence(audio, duration_seconds=30)
    wav_segments = [
        (0, _silence_wav_bytes(duration_seconds=10), True),
        (1, _silence_wav_bytes(duration_seconds=10), True),
        (2, _silence_wav_bytes(duration_seconds=10), True),
    ]
    factory_calls = []

    class FakeSegmenter:
        def iter_wav(self, path):
            assert path == audio
            yield from wav_segments

    def segmenter_factory():
        factory_calls.append(True)
        return FakeSegmenter()

    session = _Session(
        ["hello repeated phrase", "repeated phrase again", "final words"]
    )
    transcriber = GemmaAudioTranscriber(
        delete_input=False,
        session=session,
        segmenter_factory=segmenter_factory,
    )

    result = transcriber.transcribe(audio)

    assert factory_calls == [True]
    assert result.text == (
        "hello repeated phrase repeated phrase again final words"
    )
    assert result.segments == (
        "hello repeated phrase",
        "repeated phrase again",
        "final words",
    )
    assert result.segment_modes == ("ordinary", "ordinary", "ordinary")
    assert audio.exists()
    assert len(session.calls) == 3
    assert all(
        call[1]["grammar"] == r"root ::= [\x20-\x7E]*"
        for call in session.calls
    )
    assert all(
        [message["role"] for message in call[1]["messages"]] == ["user"]
        for call in session.calls
    )


def test_context_uses_up_to_2000_characters_without_changing_assembly():
    from voxd.core.gemma_transcriber import (
        PREVIOUS_CONTEXT_CHARS,
        GemmaAudioTranscriber,
    )

    previous = "startmarker " + " ".join(f"word{index}" for index in range(500))
    session = _Session([previous, "final words"])
    transcriber = GemmaAudioTranscriber(delete_input=False, session=session)

    result = transcriber.transcribe_segments(
        [
            (0, _silence_wav_bytes(duration_seconds=10), True),
            (1, _silence_wav_bytes(duration_seconds=10), True),
        ]
    )

    sent_prompt = session.calls[1][1]["messages"][0]["content"][0]["text"]
    marker = "previous accumulated transcript ends with: "
    context = sent_prompt.split(marker, 1)[1].split(
        ". Do not repeat that context", 1
    )[0]
    assert len(context) <= PREVIOUS_CONTEXT_CHARS + 2  # repr quotes
    assert "startmarker" not in context
    assert context.endswith("word499'")
    assert result.text == f"{previous} final words"


def test_context_character_cap_is_strict_for_one_oversized_token():
    from voxd.core.gemma_transcriber import GemmaAudioTranscriber

    assert GemmaAudioTranscriber._context_tail("x" * 2100) == "x" * 2000


def test_vad_negative_audio_is_transcribed_without_previous_context():
    from voxd.core.gemma_transcriber import GemmaAudioTranscriber

    session = _Session(["first words", "quiet final words"])
    transcriber = GemmaAudioTranscriber(delete_input=False, session=session)
    wav = _silence_wav_bytes(duration_seconds=1)

    result = transcriber.transcribe_segments(
        [(0, wav, True), (1, wav, False)]
    )

    second_prompt = session.calls[1][1]["messages"][0]["content"][0]["text"]
    assert "previous accumulated transcript" not in second_prompt
    assert result.text == "first words quiet final words"


def test_empty_silent_segment_is_ignored_during_direct_assembly():
    from voxd.core.gemma_transcriber import GemmaAudioTranscriber

    session = _Session(["first words", ""])
    transcriber = GemmaAudioTranscriber(delete_input=False, session=session)
    wav = _silence_wav_bytes(duration_seconds=1)

    result = transcriber.transcribe_segments(
        [(0, wav, True), (1, wav, False)]
    )

    assert len(session.calls) == 2
    assert result.text == "first words"
    assert result.segments == ("first words",)
    assert result.segment_modes == ("ordinary",)


def test_all_empty_segments_return_a_successful_no_speech_result():
    from voxd.core.gemma_transcriber import GemmaAudioTranscriber

    session = _Session(["", "   "])
    transcriber = GemmaAudioTranscriber(delete_input=False, session=session)
    wav = _silence_wav_bytes(duration_seconds=1)

    result = transcriber.transcribe_segments(
        [(0, wav, False), (1, wav, False)]
    )

    assert len(session.calls) == 2
    assert result.text == ""
    assert result.segments == ()
    assert result.segment_modes == ()


def test_empty_speech_positive_segment_retries_instead_of_being_discarded(
    monkeypatch,
):
    from voxd.core.gemma_transcriber import GemmaAudioTranscriber

    session = _Session(["", "recovered words"])
    monkeypatch.setattr("voxd.core.gemma_transcriber.time.sleep", lambda *_: None)
    transcriber = GemmaAudioTranscriber(delete_input=False, session=session)

    result = transcriber.transcribe_segments(
        [(0, _silence_wav_bytes(duration_seconds=1), True)]
    )

    assert len(session.calls) == 2
    assert result.text == "recovered words"


def test_repeated_empty_speech_positive_segment_is_an_error():
    from voxd.core.gemma_transcriber import GemmaAudioTranscriber, GemmaTranscriptionError

    transcriber = GemmaAudioTranscriber(
        delete_input=False,
        session=_Session(["", "   "]),
    )

    with pytest.raises(GemmaTranscriptionError, match="speech-positive audio"):
        transcriber.transcribe_segments(
            [(0, _silence_wav_bytes(duration_seconds=1), True)]
        )


def test_no_audio_segments_remain_an_error():
    from voxd.core.gemma_transcriber import GemmaAudioTranscriber, GemmaTranscriptionError

    transcriber = GemmaAudioTranscriber(delete_input=False, session=_Session([]))

    with pytest.raises(GemmaTranscriptionError, match="Audio input contains no frames"):
        transcriber.transcribe_segments([])


def test_gemma_request_contains_audio_and_generation_controls():
    from voxd.core.gemma_transcriber import GemmaAudioTranscriber

    session = _Session(["namaste duniya"])
    transcriber = GemmaAudioTranscriber(delete_input=False, session=session)
    wav = _silence_wav_bytes(duration_seconds=5)

    transcriber.transcribe_segments([(0, wav, True)])

    url, payload, timeout = session.calls[0]
    assert url == "http://localhost:9292/v1/chat/completions"
    assert timeout == 300
    assert payload["model"] == "gemma-e4b"
    assert payload["temperature"] == 0
    assert payload["max_tokens"] == 1024
    assert payload["grammar"] == r"root ::= [\x20-\x7E]*"
    assert payload["chat_template_kwargs"] == {"enable_thinking": False}
    audio_part = payload["messages"][0]["content"][1]
    assert audio_part["type"] == "input_audio"
    assert base64.b64decode(audio_part["input_audio"]["data"]) == wav


def test_gemma_deletes_input_only_after_complete_success(tmp_path):
    from voxd.core.gemma_transcriber import GemmaAudioTranscriber

    audio = tmp_path / "short.wav"
    _write_silence(audio, duration_seconds=1)

    class FakeSegmenter:
        def iter_wav(self, _path):
            yield 0, _silence_wav_bytes(duration_seconds=1), True

    transcriber = GemmaAudioTranscriber(
        delete_input=True,
        session=_Session(["namaste duniya"]),
        segmenter_factory=FakeSegmenter,
    )

    assert transcriber.transcribe(audio).text == "namaste duniya"
    assert not audio.exists()


def test_gemma_captures_resolved_model_identity():
    from voxd.core.gemma_transcriber import GemmaAudioTranscriber

    session = _Session(
        [
            _Response(
                "namaste duniya",
                model="gemma-e4b-q8",
                system_fingerprint="server-build-1",
            )
        ]
    )
    transcriber = GemmaAudioTranscriber(delete_input=False, session=session)

    result = transcriber.transcribe_segments(
        [(0, _silence_wav_bytes(duration_seconds=1), True)]
    )

    assert result.resolved_model == "gemma-e4b-q8"
    assert result.system_fingerprint == "server-build-1"


def test_gemma_default_session_does_not_inherit_environment_proxies():
    from voxd.core.gemma_transcriber import GemmaAudioTranscriber

    transcriber = GemmaAudioTranscriber()

    assert transcriber.session.trust_env is False


def test_gemma_warmup_sends_minimal_text_request():
    from voxd.core.gemma_transcriber import GemmaAudioTranscriber

    session = _Session(["OK"])
    transcriber = GemmaAudioTranscriber(session=session)

    transcriber.warmup()

    assert len(session.calls) == 1
    url, payload, timeout = session.calls[0]
    assert url == "http://localhost:9292/v1/chat/completions"
    assert timeout == 60
    assert payload == {
        "model": "gemma-e4b",
        "messages": [{"role": "user", "content": "Reply with OK."}],
        "stream": False,
        "temperature": 0.0,
        "max_tokens": 1,
        "chat_template_kwargs": {"enable_thinking": False},
    }


def test_default_prompt_is_scoped_computer_dictation_context():
    from voxd.core.gemma_transcriber import DEFAULT_PROMPT

    assert "ASCII Latin letters (A-Z and a-z)" in DEFAULT_PROMPT
    assert "Never output Devanagari or any other Indic script" in DEFAULT_PROMPT
    assert "transliterate every Hindi word into natural Roman Hinglish" in DEFAULT_PROMPT
    assert "live voice dictation on a computer" in DEFAULT_PROMPT
    assert "Do not answer the speaker" in DEFAULT_PROMPT
    assert "transcribe the spoken words literally" in DEFAULT_PROMPT
    assert "If the audio contains no intelligible speech, output nothing" in DEFAULT_PROMPT


def test_protocol_metadata_describes_the_vad_and_direct_append_pipeline():
    from voxd.core.gemma_transcriber import GemmaAudioTranscriber

    transcriber = GemmaAudioTranscriber(delete_input=False, session=_Session([]))

    protocol = transcriber.protocol_metadata()

    assert protocol["version"] == 6
    assert protocol["prompt"] == transcriber.prompt
    assert protocol["previous_context_max_characters"] == 2000
    assert protocol["assembly"] == "space-concatenation"
    assert protocol["silence_handling"] == "omit-context-and-allow-empty-output"
    assert protocol["generation"]["grammar"] == r"root ::= [\x20-\x7E]*"
    assert protocol["segmentation"] == {
        "algorithm": "silero-minimum-local-speech-risk",
        "target_seconds": 10.0,
        "search_radius_seconds": 2.0,
        "analysis_sample_rate": 16000,
        "analysis_frame_samples": 512,
        "local_window_ms": 96.0,
        "decision_lookahead_ms": 32.0,
        "speech_threshold": 0.5,
        "model": "silero_vad_16k_op15.onnx",
        "model_sha256": (
            "7ed98ddbad84ccac4cd0aeb3099049280713df825c610a8ed34543318f1b2c49"
        ),
    }


def test_gemma_failure_retries_and_retains_audio(monkeypatch, tmp_path):
    from voxd.core.gemma_transcriber import GemmaAudioTranscriber, GemmaTranscriptionError

    class FailingSession:
        def __init__(self):
            self.calls = 0

        def post(self, *args, **kwargs):
            self.calls += 1
            raise requests.ConnectionError("offline")

    audio = tmp_path / "failed.wav"
    _write_silence(audio, duration_seconds=1)

    class FakeSegmenter:
        def iter_wav(self, _path):
            yield 0, _silence_wav_bytes(duration_seconds=1), True

    session = FailingSession()
    monkeypatch.setattr("voxd.core.gemma_transcriber.time.sleep", lambda *_: None)
    transcriber = GemmaAudioTranscriber(
        delete_input=True,
        attempts=2,
        session=session,
        segmenter_factory=FakeSegmenter,
    )

    with pytest.raises(GemmaTranscriptionError, match="Segment 1 failed"):
        transcriber.transcribe(audio)

    assert session.calls == 2
    assert audio.exists()


@pytest.mark.parametrize("segment_seconds", [0, 2, 27.99, 28, 30])
def test_gemma_rejects_invalid_segment_target(segment_seconds):
    from voxd.core.gemma_transcriber import GemmaAudioTranscriber

    with pytest.raises(ValueError):
        GemmaAudioTranscriber(segment_seconds=segment_seconds)
