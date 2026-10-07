from app.conversion.backends import DoclingBackend, PlainTextBackend
from app.tasks.convert import _convert_fast_backend


def test_plain_text_and_csv_route_to_plaintext_backend():
    assert isinstance(_convert_fast_backend("text/plain"), PlainTextBackend)
    assert isinstance(_convert_fast_backend("text/csv"), PlainTextBackend)


def test_everything_else_routes_to_docling():
    assert isinstance(_convert_fast_backend("application/pdf"), DoclingBackend)
    assert isinstance(_convert_fast_backend("text/html"), DoclingBackend)
    assert isinstance(
        _convert_fast_backend("application/vnd.openxmlformats-officedocument.wordprocessingml.document"),
        DoclingBackend,
    )
