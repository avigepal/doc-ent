from app.history.store import delete_conversation


class _ExplodingSession:
    def execute(self, *args, **kwargs):
        raise AssertionError("must not touch the database")

    commit = execute


def test_deleting_the_empty_id_is_refused_without_touching_the_db():
    # '' is the bucket of legacy rows from before chats existed — deleting
    # "conversation ''" would silently wipe all of them.
    assert delete_conversation(_ExplodingSession(), "") == 0
