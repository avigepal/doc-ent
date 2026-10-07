from app.ingestion.summary import summarize
from app.ingestion.walker import ScannedFile


def test_summarize_counts_files_bytes_and_breakdowns():
    files = [
        ScannedFile("a.pdf", "h1", "application/pdf", 100, "convert_fast"),
        ScannedFile("b.pdf", "h2", "application/pdf", 200, "convert_fast"),
        ScannedFile("c.png", "h3", "image/png", 50, "convert_vision"),
    ]

    result = summarize(files)

    assert result.total_files == 3
    assert result.total_bytes == 350
    assert result.by_queue == {"convert_fast": 2, "convert_vision": 1}
    assert result.by_mime == {"application/pdf": 2, "image/png": 1}


def test_summarize_handles_empty_list():
    result = summarize([])
    assert result.total_files == 0
    assert result.total_bytes == 0
    assert result.by_queue == {}
    assert result.by_mime == {}
