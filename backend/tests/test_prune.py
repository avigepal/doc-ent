from app.ingestion.prune import paths_to_prune


def test_prunes_records_whose_file_is_gone():
    assert paths_to_prune({"/raw/a.pdf", "/raw/b.pdf"}, {"/raw/a.pdf"}) == {"/raw/b.pdf"}


def test_keeps_everything_still_on_disk():
    assert paths_to_prune({"/raw/a.pdf"}, {"/raw/a.pdf", "/raw/new.pdf"}) == set()


def test_empty_scan_prunes_nothing_in_case_the_mount_is_down():
    assert paths_to_prune({"/raw/a.pdf", "/raw/b.pdf"}, set()) == set()
