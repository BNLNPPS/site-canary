"""Queue-name classification shared by assessment and presentation."""


def is_test_queue(name):
    """Return whether a PanDA queue is explicitly test-named."""
    return 'test' in (name or '').casefold()
