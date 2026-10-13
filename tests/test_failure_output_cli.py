"""Exercise the native CLI and real SDK against an isolated local Conductor substitute."""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest


@pytest.mark.parametrize("mode", ["thread", "process"])
@pytest.mark.parametrize("terminal", [False, True])
def test_native_start_publishes_one_failure_update(tmp_path, mode, terminal) -> None:
    """Failure output reaches HTTP through perago start without a runner override or second update."""

    updates, paths = [], []
    received = threading.Event()
    polled = False

    class Handler(BaseHTTPRequestHandler):
        """Provide metadata and one task, with no external API or provider dependency."""

        def _reply(self, payload):
            """Return ordinary Conductor response JSON to the real SDK."""

            body = json.dumps(payload).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            """Supply metadata or the single in-memory poll result."""

            nonlocal polled
            paths.append(self.path)
            if "/metadata/taskdefs/" in self.path:
                self._reply({"name": "tests.structuredFailure", "retryCount": 0, "timeoutSeconds": 60})
            elif "/tasks/poll/batch/" in self.path:
                payload = [] if polled else [{"taskId": "task-fixture", "workflowInstanceId": "workflow-fixture",
                                              "taskDefName": "tests.structuredFailure", "referenceTaskName": "fixture",
                                              "seq": 1, "iteration": 0, "retryCount": 0, "status": "IN_PROGRESS",
                                              "inputData": {"params": {"terminal": terminal}}}]
                polled = True
                self._reply(payload)
            else:
                self.send_error(404)

        def do_POST(self):
            """Capture the native TaskRunner's one result update."""

            updates.append(json.loads(self.rfile.read(int(self.headers["Content-Length"]))))
            self._reply(None if self.path.endswith("/update-v2") else "task-fixture")
            received.set()

        def log_message(self, *args):
            """Keep local HTTP requests out of the test console."""

            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    root = Path(__file__).resolve().parents[1]
    # Isolated cwd and cleared operational env prevent a developer's live configuration leaking into the test.
    env = {name: value for name, value in os.environ.items() if not name.startswith(("CONDUCTOR_", "PERAGO_", "OTEL_"))}
    env.update(CONDUCTOR_SERVER_URL=f"http://127.0.0.1:{server.server_port}/api",
               PERAGO_LOG_ROOT=str(tmp_path / "logs"), PERAGO_WORKSPACE_ROOT=str(tmp_path / "workspaces"),
               PERAGO_SHUTDOWN_FORCE_KILL_AFTER="2s", PYTHONPATH=os.pathsep.join([str(root / "src"), str(root / "tests/fixtures")]))
    # A file avoids relying on stdout EOF shared by the native supervisor and its descendants.
    log_path = tmp_path / "native-start.log"
    log = log_path.open("w")
    process = subprocess.Popen([sys.executable, "-m", "perago.cli", "start", "structured_failure", "-j", "1", "--execution-mode", mode],
                               cwd=tmp_path, env=env, stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
    try:
        assert received.wait(20), f"native start did not publish: exit={process.poll()}, paths={paths}"
        # This test covers publication. Native graceful-shutdown behavior has its own supervisor tests.
        assert len(updates) == 1
        assert updates[0]["status"] == ("FAILED_WITH_TERMINAL_ERROR" if terminal else "FAILED")
        assert updates[0]["reasonForIncompletion"] == "provider unavailable"
        assert updates[0]["outputData"]["error"]["code"] == "SERVICE_FAILURE"
        assert "result" not in updates[0]["outputData"]
    finally:
        # Bound fixture cleanup to its own process group without changing production supervision.
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        process.wait(timeout=5)
        log.close()
        server.shutdown()
        server.server_close()
        thread.join()
