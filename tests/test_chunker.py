from apps.api.ingest.chunker import chunk_document


def test_headings_start_new_chunks_and_are_remembered():
    body = "# Fees\n\nPay by 15 November.\n\n# Hostel\n\nGates close at 22:00."
    chunks = chunk_document(body)
    assert [(c.heading, c.text) for c in chunks] == [
        ("Fees", "Pay by 15 November."),
        ("Hostel", "Gates close at 22:00."),
    ]


def test_long_sections_are_split_with_overlap():
    sentences = [f"Sentence number {i} has exactly seven words." for i in range(100)]
    chunks = chunk_document("# Long\n\n" + " ".join(sentences), max_words=70, overlap_words=15)
    assert len(chunks) > 5
    assert all(len(c.text.split()) <= 70 + 15 for c in chunks)
    # The start of each chunk repeats the end of the one before it.
    for previous, current in zip(chunks, chunks[1:], strict=False):
        assert current.text.split(".")[0] in previous.text


def test_devanagari_sentence_breaks():
    body = "पहला वाक्य यहाँ है। " * 60
    chunks = chunk_document(body, max_words=50, overlap_words=0)
    assert len(chunks) > 1
    assert all(c.text.rstrip().endswith("।") for c in chunks)
