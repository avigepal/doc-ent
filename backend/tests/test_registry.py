from app.handlers.registry import route_mime


def test_office_and_pdf_go_to_fast_conversion():
    assert route_mime("application/pdf") == "convert_fast"
    assert route_mime("application/vnd.openxmlformats-officedocument.wordprocessingml.document") == "convert_fast"
    assert route_mime("application/vnd.openxmlformats-officedocument.spreadsheetml.sheet") == "convert_fast"
    assert route_mime("text/html") == "convert_fast"


def test_images_go_to_vision_queue():
    assert route_mime("image/png") == "convert_vision"
    assert route_mime("image/jpeg") == "convert_vision"


def test_email_archive_formats_go_to_email_queue():
    assert route_mime("application/mbox") == "convert_email_archive"
    assert route_mime("message/rfc822") == "convert_email_archive"
    assert route_mime("application/vnd.ms-outlook") == "convert_email_archive"

    # routed by file extension when magic reports a generic octet-stream, e.g. .pst
    assert route_mime("application/octet-stream", filename="archive.pst") == "convert_email_archive"


def test_unknown_mime_falls_back_to_catch_all():
    assert route_mime("application/x-totally-unknown") == "convert_fast"


def test_scanned_pdf_hint_still_routes_through_fast_queue_initially():
    # OCR-vs-text routing happens inside convert_fast's text-density probe (phase 2),
    # not at registry time — the registry only picks the entry queue.
    assert route_mime("application/pdf", filename="scanned.pdf") == "convert_fast"
