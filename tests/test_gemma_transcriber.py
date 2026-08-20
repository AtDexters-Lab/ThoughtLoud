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


def test_gemma_segments_long_wav_and_merges_overlap(tmp_path):
    from voxd.core.gemma_transcriber import GemmaAudioTranscriber

    audio = tmp_path / "long.wav"
    _write_silence(audio, duration_seconds=60)
    session = _Session([
        "hello duniya kaise ho",
        "kaise ho main theek hoon",
        "theek hoon dhanyavaad",
    ])
    transcriber = GemmaAudioTranscriber(
        server_url="http://localhost:9292/",
        model="gemma-e4b",
        segment_seconds=25,
        overlap_seconds=1,
        delete_input=False,
        session=session,
    )

    result = transcriber.transcribe(audio)

    assert result.text == "hello duniya kaise ho main theek hoon dhanyavaad"
    assert list(result.segments) == [
        "hello duniya kaise ho",
        "kaise ho main theek hoon",
        "theek hoon dhanyavaad",
    ]
    assert audio.exists()
    assert len(session.calls) == 3

    durations = []
    for url, payload, timeout in session.calls:
        assert url == "http://localhost:9292/v1/chat/completions"
        assert timeout == 300
        assert payload["model"] == "gemma-e4b"
        assert payload["chat_template_kwargs"] == {"enable_thinking": False}
        assert payload["messages"][0]["content"][0]["type"] == "text"
        audio_part = payload["messages"][0]["content"][1]
        assert audio_part["type"] == "input_audio"
        wav_bytes = base64.b64decode(audio_part["input_audio"]["data"])
        with wave.open(io.BytesIO(wav_bytes), "rb") as chunk:
            durations.append(chunk.getnframes() / chunk.getframerate())

    assert durations == [25, 25, 12]
    second_prompt = session.calls[1][1]["messages"][0]["content"][0]["text"]
    assert "previous segment ended with" in second_prompt


def test_gemma_transcribes_live_segments_sequentially():
    from voxd.core.gemma_transcriber import GemmaAudioTranscriber

    session = _Session([
        "hello duniya kaise ho",
        "kaise ho main theek hoon",
    ])
    transcriber = GemmaAudioTranscriber(delete_input=False, session=session)

    result = transcriber.transcribe_segments(
        [
            (0, _silence_wav_bytes(duration_seconds=25)),
            (1, _silence_wav_bytes(duration_seconds=5)),
        ]
    )

    assert result.text == "hello duniya kaise ho main theek hoon"
    assert list(result.segments) == [
        "hello duniya kaise ho",
        "kaise ho main theek hoon",
    ]
    assert len(session.calls) == 2
    second_prompt = session.calls[1][1]["messages"][0]["content"][0]["text"]
    assert "previous segment ended with" in second_prompt


def test_gemma_deletes_input_only_after_complete_success(tmp_path):
    from voxd.core.gemma_transcriber import GemmaAudioTranscriber

    audio = tmp_path / "short.wav"
    _write_silence(audio, duration_seconds=1)
    transcriber = GemmaAudioTranscriber(
        delete_input=True,
        session=_Session(["namaste duniya"]),
    )

    assert transcriber.transcribe(audio).text == "namaste duniya"
    assert not audio.exists()


def test_gemma_captures_resolved_model_identity(tmp_path):
    from voxd.core.gemma_transcriber import GemmaAudioTranscriber

    audio = tmp_path / "identity.wav"
    _write_silence(audio, duration_seconds=1)
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

    result = transcriber.transcribe(audio)

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
    assert "coding tools" in DEFAULT_PROMPT
    assert "web searches" in DEFAULT_PROMPT
    assert "only to resolve likely words and sentence boundaries" in DEFAULT_PROMPT
    assert "Do not answer the speaker" in DEFAULT_PROMPT
    assert "Add readable punctuation" in DEFAULT_PROMPT


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
    session = FailingSession()
    monkeypatch.setattr("voxd.core.gemma_transcriber.time.sleep", lambda *_: None)
    transcriber = GemmaAudioTranscriber(delete_input=True, attempts=2, session=session)

    with pytest.raises(GemmaTranscriptionError, match="Segment 1 failed"):
        transcriber.transcribe(audio)

    assert session.calls == 2
    assert audio.exists()


@pytest.mark.parametrize(
    ("segment_seconds", "overlap_seconds"),
    [(30, 1), (0, 0), (25, 25), (25, -1)],
)
def test_gemma_rejects_invalid_segment_window(segment_seconds, overlap_seconds):
    from voxd.core.gemma_transcriber import GemmaAudioTranscriber

    with pytest.raises(ValueError):
        GemmaAudioTranscriber(
            segment_seconds=segment_seconds,
            overlap_seconds=overlap_seconds,
        )
