"""Regression tests for the AiWorker thread lifecycle (issue #85).

The AI worker runs on a *real* ``QThread``. If ``MainWindow`` keeps no strong
reference to it, the worker is garbage-collected before ``thread.started ->
worker.run`` can fire — so no result is ever emitted and every in-app AI call
silently hangs. The rest of the AI suite mocks ``_make_ai_worker`` / uses the
``run_with`` seam, so it never exercises this path; these tests do, by driving
the real thread with a stubbed ``chat`` and asserting the worker survives long
enough to emit.
"""

import gc
import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6")

from PySide6.QtCore import QEventLoop, QThread, QTimer  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

import core.ai_client as ai_client  # noqa: E402
from ui.main_window import MainWindow  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    yield QApplication.instance() or QApplication([])


def _pump(predicate, *, timeout_ms=3000):
    """Spin the Qt event loop until ``predicate()`` is true or the timeout hits."""
    loop = QEventLoop()
    QTimer.singleShot(timeout_ms, loop.quit)
    ticker = QTimer()
    ticker.timeout.connect(lambda: predicate() and loop.quit())
    ticker.start(20)
    loop.exec()
    ticker.stop()


def test_worker_survives_until_it_emits(qapp, monkeypatch):
    """A worker from _make_ai_worker must emit even when the caller keeps no
    local reference to it (the real callers don't) — regression for #85."""
    monkeypatch.setattr(ai_client, "chat", lambda key, msgs, *, timeout=120.0: "PONG")
    window = MainWindow()
    got: dict[str, str] = {}

    worker, thread = window._make_ai_worker("key", [{"role": "user", "content": "hi"}])
    worker.finished.connect(lambda r: (got.__setitem__("reply", r), thread.quit()))
    worker.error.connect(lambda m: (got.__setitem__("error", m), thread.quit()))
    thread.started.connect(worker.run)
    thread.start()

    # Simulate the real callers (open_test_connection / _send_chat) returning:
    # the only local strong reference to the worker is dropped here.
    del worker
    gc.collect()

    _pump(lambda: bool(got))

    assert got.get("reply") == "PONG", (
        f"worker emitted nothing (got={got!r}) — it was garbage-collected before run()"
    )


def test_finished_job_is_released(qapp, monkeypatch):
    """After the thread finishes, the kept reference is released (no leak)."""
    monkeypatch.setattr(ai_client, "chat", lambda key, msgs, *, timeout=120.0: "PONG")
    window = MainWindow()
    got: dict[str, str] = {}

    worker, thread = window._make_ai_worker("key", [{"role": "user", "content": "hi"}])
    worker.finished.connect(lambda r: (got.__setitem__("reply", r), thread.quit()))
    thread.started.connect(worker.run)
    thread.start()
    del worker
    gc.collect()

    _pump(lambda: bool(got))
    assert got.get("reply") == "PONG"
    # The job is dropped once the thread finishes (give the finished signal a tick).
    _pump(lambda: len(window._ai_jobs) == 0, timeout_ms=1000)
    assert window._ai_jobs == set()


def test_result_callback_runs_on_main_thread(qapp, monkeypatch):
    """Regression for #87: the per-call result callback MUST run on the main
    (GUI) thread. Connecting worker signals to local closures delivers them on
    the worker thread, so any GUI call (QMessageBox) from a handler crashes the
    app. The fix routes results through MainWindow bound-method slots."""
    main_thread = QApplication.instance().thread()
    monkeypatch.setattr(ai_client, "chat", lambda k, m, *, timeout=120.0: "PONG")
    window = MainWindow()
    seen: dict[str, object] = {}

    worker, thread = window._make_ai_worker("key", [{"role": "user", "content": "hi"}])
    worker._main_on_reply = lambda reply: seen.update(
        on_main=(QThread.currentThread() is main_thread), reply=reply
    )
    worker._main_on_error = lambda msg: seen.update(error=msg)
    worker.finished.connect(window._on_ai_finished)
    worker.error.connect(window._on_ai_error)
    thread.started.connect(worker.run)
    thread.start()
    del worker
    gc.collect()

    _pump(lambda: bool(seen))
    assert seen.get("reply") == "PONG"
    assert seen.get("on_main") is True, "result callback ran off the main thread — would crash the GUI"
