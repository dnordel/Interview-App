from transcript_accumulator import append_candidate_segment_text


class Seg:
    def __init__(self, speaker: str, text: str):
        self.speaker = speaker
        self.text = text


def test_append_candidate_segment_text_adds_candidate_only() -> None:
    existing = "hello"
    segments = [
        Seg("INTERVIEWER", "question one"),
        Seg("CANDIDATE", "answer one"),
        Seg("CANDIDATE", "answer two"),
    ]

    merged = append_candidate_segment_text(existing, segments, candidate_label="CANDIDATE")

    assert merged == "hello answer one answer two"


def test_append_candidate_segment_text_accepts_dict_segments() -> None:
    existing = ""
    segments = [
        {"speaker": "CANDIDATE", "text": "new answer"},
        {"speaker": "INTERVIEWER", "text": "prompt"},
    ]

    merged = append_candidate_segment_text(existing, segments, candidate_label="CANDIDATE")

    assert merged == "new answer"


def test_append_candidate_segment_text_returns_existing_when_no_match() -> None:
    existing = "already there"
    segments = [Seg("INTERVIEWER", "only prompt")]

    merged = append_candidate_segment_text(existing, segments, candidate_label="CANDIDATE")

    assert merged == "already there"
