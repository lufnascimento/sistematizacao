import sys
import tempfile
import time
import threading
import unittest
from pathlib import Path
from unittest.mock import Mock

from terraflux_api.process_runner import EngineCancelled, run_engine
from terraflux_api.jobs import JobRunner


class ProcessRunnerTests(unittest.TestCase):
    def test_stdout_and_stderr_are_streamed(self):
        with tempfile.TemporaryDirectory() as folder:
            events = []
            run_engine([sys.executable, "-c", "import sys; print('progress'); print('diagnostic', file=sys.stderr)"],
                       Path(folder), "test", lambda: False, lambda *event: events.append(event), timeout_seconds=10)
            self.assertIn(("INFO", "progress"), events)
            self.assertIn(("ENGINE", "diagnostic"), events)

    def test_cancel_terminates_running_engine(self):
        with tempfile.TemporaryDirectory() as folder:
            started = time.monotonic()
            with self.assertRaises(EngineCancelled):
                run_engine([sys.executable, "-c", "import time; time.sleep(60)"], Path(folder), "test",
                           lambda: time.monotonic() - started > .3, lambda *_: None, timeout_seconds=10)
            self.assertLess(time.monotonic() - started, 8)

    def test_timeout_does_not_wait_for_long_engine(self):
        with tempfile.TemporaryDirectory() as folder:
            started = time.monotonic()
            with self.assertRaises(TimeoutError):
                run_engine([sys.executable, "-c", "import time; time.sleep(60)"], Path(folder), "test",
                           lambda: False, lambda *_: None, timeout_seconds=.3)
            self.assertLess(time.monotonic() - started, 8)

    def test_nonzero_exit_is_failure(self):
        with tempfile.TemporaryDirectory() as folder:
            with self.assertRaisesRegex(RuntimeError, "code 7"):
                run_engine([sys.executable, "-c", "raise SystemExit(7)"], Path(folder), "test", lambda: False, lambda *_: None)

    def test_cancel_stops_grandchild_holding_output_pipe(self):
        with tempfile.TemporaryDirectory() as folder:
            events = []
            command = "import subprocess,sys,time; subprocess.Popen([sys.executable,'-c','import time; time.sleep(60)']); print('ready',flush=True); time.sleep(60)"
            with self.assertRaises(EngineCancelled):
                run_engine([sys.executable, "-c", command], Path(folder), "test", lambda: bool(events),
                           lambda *event: events.append(event), timeout_seconds=10)
            self.assertFalse(any(thread.name.startswith("engine-output-") for thread in threading.enumerate()))

    def test_worker_preserves_cancelled_status_and_discards_publications(self):
        with tempfile.TemporaryDirectory() as folder:
            store = Mock()
            store.get.return_value = {"id": "run", "project_id": "project", "engine_id": "validate_uploads"}
            runner = JobRunner(store, Path(folder))
            runner._set = Mock()
            runner._log = Mock()
            runner._discard_run_publications = Mock()
            runner._engines["validate_uploads"] = Mock(side_effect=EngineCancelled("cancelled"))
            runner._execute("run")
            self.assertEqual(runner._set.call_args.kwargs["status"], "CANCELLED")
            runner._discard_run_publications.assert_called_once_with("run")
