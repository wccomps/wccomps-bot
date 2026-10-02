"""run_detached: a streamed operation finishes even when nobody reads the rest of its stream."""

import json
import threading
from collections.abc import Iterator

from core.utils import run_detached


def _join() -> None:
    for thread in threading.enumerate():
        if thread.name == "run-detached":
            thread.join(timeout=10)


def test_relays_every_line_in_order():
    assert list(run_detached(iter(["a\n", "b\n", "c\n"]))) == ["a\n", "b\n", "c\n"]


def test_operation_finishes_after_the_reader_stops():
    done = []
    reader_gone = threading.Event()

    def operation() -> Iterator[str]:
        for n in range(3):
            if n:
                reader_gone.wait(timeout=10)
            done.append(n)
            yield f"{n}\n"

    stream = run_detached(operation())
    assert next(stream) == "0\n"
    stream.close()
    reader_gone.set()
    _join()

    assert done == [0, 1, 2]


def test_a_failing_operation_ends_the_stream_with_an_error():
    def operation() -> Iterator[str]:
        yield "started\n"
        raise RuntimeError("Authentik is down")

    lines = list(run_detached(operation()))

    assert lines[0] == "started\n"
    assert json.loads(lines[-1]) == {"done": True, "success": False, "message": "Failed: Authentik is down"}
