"""Stream bounded engine output and stop the whole child tree on cancellation."""

import math
import os
import queue
import signal
import subprocess
import threading
import time


class EngineCancelled(RuntimeError):
    pass


def _stop_tree(process):
    if os.name == "nt":
        subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                       creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0), check=False, timeout=10)
    else:
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        if os.name != "nt":
            os.killpg(process.pid, signal.SIGKILL)
        else:
            process.kill()
        process.wait(timeout=5)


def run_engine(command, cwd, label, cancelled, log, timeout_seconds=21600):
    if not math.isfinite(timeout_seconds) or timeout_seconds <= 0:
        raise ValueError("Engine timeout must be finite and positive")
    if cancelled():
        raise EngineCancelled("Execucao cancelada antes de iniciar o motor.")
    environment = {**os.environ, "PYTHONUNBUFFERED": "1"}
    process = subprocess.Popen(command, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                               text=True, encoding="utf-8", errors="replace", env=environment,
                               creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                               start_new_session=os.name != "nt")
    events = queue.Queue(maxsize=256)
    stopping = threading.Event()

    def read(stream, level):
        try:
            for line in iter(stream.readline, ""):
                while not stopping.is_set():
                    try:
                        events.put((level, line.rstrip()), timeout=.1)
                        break
                    except queue.Full:
                        pass
                if stopping.is_set():
                    break
        finally:
            stream.close()

    readers = [threading.Thread(target=read, args=(stream, level), daemon=True, name=f"engine-output-{process.pid}-{level}")
               for stream, level in ((process.stdout, "INFO"), (process.stderr, "ENGINE"))]
    for reader in readers:
        reader.start()
    started = time.monotonic()
    checked = started
    try:
        while process.poll() is None or any(reader.is_alive() for reader in readers) or not events.empty():
            now = time.monotonic()
            if now - checked >= .25:
                checked = now
                if cancelled():
                    raise EngineCancelled("Execucao cancelada durante o processamento.")
                if now - started > timeout_seconds:
                    raise TimeoutError(f"{label} exceeded the configured engine timeout")
            try:
                level, message = events.get(timeout=.1)
            except queue.Empty:
                continue
            if message:
                log(level, message)
        if process.returncode != 0:
            raise RuntimeError(f"{label} engine exited with code {process.returncode}")
    finally:
        stopping.set()
        if process.poll() is None or any(reader.is_alive() for reader in readers):
            _stop_tree(process)
        for reader in readers:
            reader.join(timeout=2)
