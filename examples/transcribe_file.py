#!/usr/bin/env python3
"""Transcribe an existing mono 16-bit PCM WAV without the desktop application."""
from __future__ import annotations

import argparse
from pathlib import Path
import sys


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("audio", type=Path, help="mono, uncompressed 16-bit PCM WAV")
    parser.add_argument(
        "--endpoint", default="http://localhost:9292",
        help="Gemma service base URL (default: http://localhost:9292)",
    )
    parser.add_argument("--model", default="gemma-e4b", help="endpoint model name")
    parser.add_argument(
        "--speech-preferences", default="",
        help="optional description of usual languages and terminology",
    )
    args = parser.parse_args()
    if not args.audio.is_file():
        parser.error(f"audio file does not exist: {args.audio}")

    from voxd.core.gemma_transcriber import GemmaAudioTranscriber

    transcriber = GemmaAudioTranscriber(
        server_url=args.endpoint,
        model=args.model,
        speech_preferences=args.speech_preferences,
        delete_input=False,  # An example should never delete the supplied recording.
    )
    try:
        result = transcriber.transcribe(args.audio)
    except Exception as exc:
        print(f"Transcription failed: {exc}", file=sys.stderr)
        return 1
    print(result.text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
