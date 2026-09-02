import random


def test_streaming_assembler_commits_only_after_exact_multiword_agreement():
    from voxd.core.streaming_assembler import StreamingTranscriptAssembler

    assembler = StreamingTranscriptAssembler()

    first = assembler.observe("hello, world how are")
    second = assembler.observe("How are you today")
    third = assembler.observe("you today friend")
    final = assembler.finish()

    assert first.committed_delta == ""
    assert first.provisional_tail == "hello, world how are"
    assert second.overlap_words == 2
    assert second.committed_delta == "hello, world how are"
    assert second.committed_text == "hello, world how are"
    assert second.provisional_tail == "you today"
    assert third.committed_delta == "you today"
    assert third.committed_text == "hello, world how are you today"
    assert third.provisional_tail == "friend"
    assert final.text == "hello, world how are you today friend"
    assert final.stalled_boundaries == 0


def test_streaming_assembler_never_commits_past_an_ambiguous_boundary():
    from voxd.core.streaming_assembler import StreamingTranscriptAssembler

    assembler = StreamingTranscriptAssembler()

    assembler.observe("alpha beta gamma")
    ambiguous = assembler.observe("gamma delta")
    later_match = assembler.observe("gamma delta epsilon")
    final = assembler.finish()

    assert ambiguous.boundary_matched is False
    assert ambiguous.commits_blocked is True
    assert ambiguous.committed_delta == ""
    assert later_match.boundary_matched is True
    assert later_match.commits_blocked is True
    assert later_match.committed_delta == ""
    assert final.committed_text == ""
    assert final.provisional_tail == "alpha beta gamma gamma delta epsilon"
    assert final.stalled_boundaries == 1


def test_streaming_assembler_is_monotonic_and_matches_existing_merge_property():
    from voxd.core.streaming_assembler import (
        StreamingTranscriptAssembler,
        merge_transcripts,
    )

    generator = random.Random(20260901)
    vocabulary = [f"word{index}" for index in range(20)]

    for _ in range(200):
        segments = []
        current = generator.sample(vocabulary, generator.randint(3, 8))
        segments.append(" ".join(current))
        for _ in range(generator.randint(0, 7)):
            if generator.random() < 0.1:
                segments.append("")
                continue
            if generator.random() < 0.7:
                overlap = generator.randint(2, min(4, len(current)))
                incoming = current[-overlap:] + generator.sample(
                    vocabulary, generator.randint(1, 5)
                )
            else:
                incoming = generator.sample(vocabulary, generator.randint(2, 7))
            current = [*current, *incoming]
            segments.append(" ".join(incoming))

        assembler = StreamingTranscriptAssembler()
        emitted = []
        previous_committed = ""
        for segment in segments:
            event = assembler.observe(segment)
            if event.committed_delta:
                emitted.append(event.committed_delta)
            assert event.committed_text == " ".join(emitted)
            assert event.committed_text.startswith(previous_committed)
            previous_committed = event.committed_text

        assert assembler.finish().text == merge_transcripts(segments)


def test_streaming_assembler_preserves_empty_observation_without_committing():
    from voxd.core.streaming_assembler import (
        StreamingTranscriptAssembler,
        merge_transcripts,
    )

    segments = ["alpha beta gamma", "  ", "beta gamma delta"]
    assembler = StreamingTranscriptAssembler()

    events = [assembler.observe(segment) for segment in segments]
    final = assembler.finish()

    assert events[1].text == "alpha beta gamma"
    assert events[1].commits_blocked is True
    assert events[2].boundary_matched is True
    assert events[2].committed_delta == ""
    assert final.text == merge_transcripts(segments)
    assert final.text == "alpha beta gamma delta"


def test_streaming_assembler_allows_text_after_initial_empty_observation():
    from voxd.core.streaming_assembler import (
        StreamingTranscriptAssembler,
        merge_transcripts,
    )

    segments = ["", "hello world"]
    assembler = StreamingTranscriptAssembler()

    events = [assembler.observe(segment) for segment in segments]

    assert events[0].commits_blocked is True
    assert events[1].committed_delta == ""
    assert assembler.finish().text == merge_transcripts(segments) == "hello world"
