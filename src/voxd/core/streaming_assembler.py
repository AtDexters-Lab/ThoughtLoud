from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable


def normalize_word(word: str) -> str:
    normalized = re.sub(r"[^\w]+", "", word, flags=re.UNICODE).casefold()
    return normalized or word.casefold()


def overlap_word_count(previous: list[str], incoming: list[str]) -> int:
    maximum = min(50, len(previous), len(incoming))
    for size in range(maximum, 1, -1):
        left = [normalize_word(word) for word in previous[-size:]]
        right = [normalize_word(word) for word in incoming[:size]]
        if left == right:
            return size
    return 0


def merge_transcripts(transcripts: Iterable[str]) -> str:
    iterator = iter(transcripts)
    try:
        merged = next(iterator).split()
    except StopIteration:
        return ""

    for transcript in iterator:
        incoming = transcript.split()
        duplicate_words = overlap_word_count(merged, incoming)
        merged.extend(incoming[duplicate_words:])
    return " ".join(merged).strip()


@dataclass(frozen=True)
class StreamingAssemblyEvent:
    """One shadow observation; committed text is append-only."""

    committed_delta: str
    committed_text: str
    provisional_tail: str
    overlap_words: int
    boundary_matched: bool
    commits_blocked: bool

    @property
    def text(self) -> str:
        return " ".join(
            part for part in (self.committed_text, self.provisional_tail) if part
        )


@dataclass(frozen=True)
class StreamingAssembly:
    committed_text: str
    provisional_tail: str
    stalled_boundaries: int

    @property
    def text(self) -> str:
        return " ".join(
            part for part in (self.committed_text, self.provisional_tail) if part
        )


class StreamingTranscriptAssembler:
    """Conservatively identify append-only text without changing final merging.

    An exact normalized multi-word suffix/prefix match closes a boundary. Until
    that happens, the latest segment remains provisional. Once any boundary is
    ambiguous, later text also remains provisional because an append-only sink
    could no longer safely cross that unresolved boundary.
    """

    def __init__(self) -> None:
        self._committed: list[str] = []
        self._provisional: list[str] = []
        self._observations = 0
        self._commits_blocked = False
        self._stalled_boundaries = 0

    def observe(self, transcript: str) -> StreamingAssemblyEvent:
        incoming = transcript.split()
        if not incoming:
            if self._observations > 0:
                self._stalled_boundaries += 1
            self._commits_blocked = True
            self._observations += 1
            return self._event(
                committed_delta=[],
                overlap_words=0,
                boundary_matched=False,
            )

        if self._observations == 0:
            self._provisional = incoming
            self._observations = 1
            return self._event(
                committed_delta=[],
                overlap_words=0,
                boundary_matched=False,
            )

        assembled = [*self._committed, *self._provisional]
        overlap_words = overlap_word_count(assembled, incoming)
        boundary_matched = overlap_words >= 2
        committed_delta: list[str] = []

        if boundary_matched:
            if not self._commits_blocked:
                committed_delta = self._provisional
                self._committed.extend(committed_delta)
                self._provisional = incoming[overlap_words:]
            else:
                self._provisional.extend(incoming[overlap_words:])
        else:
            self._commits_blocked = True
            self._stalled_boundaries += 1
            self._provisional.extend(incoming)

        self._observations += 1
        return self._event(
            committed_delta=committed_delta,
            overlap_words=overlap_words,
            boundary_matched=boundary_matched,
        )

    def finish(self) -> StreamingAssembly:
        return StreamingAssembly(
            committed_text=" ".join(self._committed),
            provisional_tail=" ".join(self._provisional),
            stalled_boundaries=self._stalled_boundaries,
        )

    def _event(
        self,
        *,
        committed_delta: list[str],
        overlap_words: int,
        boundary_matched: bool,
    ) -> StreamingAssemblyEvent:
        return StreamingAssemblyEvent(
            committed_delta=" ".join(committed_delta),
            committed_text=" ".join(self._committed),
            provisional_tail=" ".join(self._provisional),
            overlap_words=overlap_words,
            boundary_matched=boundary_matched,
            commits_blocked=self._commits_blocked,
        )
