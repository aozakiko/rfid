"""Cancellable subprocess execution shared by experiment task adapters."""
import os
import queue
import signal
import subprocess
import threading
import time


def check_cancelled(update):
    check = getattr(update, "check_cancelled", None)
    if check:
        check()


def run_process(command, *, cwd, timeout=1800, update=None, env=None, stage="experiment"):
    check_cancelled(update)
    started = time.monotonic()
    lines = queue.Queue()
    logs, size = [], 0
    process = subprocess.Popen(command, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, encoding="utf-8", errors="replace", env={**os.environ, "PYTHONIOENCODING": "utf-8", **(env or {})},
        **({"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {"start_new_session": True}))
    def read():
        try:
            while True:
                line = process.stdout.readline(8192)
                if not line:
                    break
                lines.put(line)
        finally:
            lines.put(None)
    reader = threading.Thread(target=read, daemon=True)
    reader.start()
    try:
        while True:
            check_cancelled(update)
            if time.monotonic() - started > timeout:
                raise TimeoutError("experiment_timeout")
            try:
                line = lines.get(timeout=.1)
            except queue.Empty:
                continue
            if line is None:
                break
            size += len(line)
            if size > 4_000_000:
                raise RuntimeError("experiment_output_limit")
            logs.append(line)
            if update:
                update(stage=stage, log=line.rstrip())
        process.wait(timeout=max(.1, timeout - (time.monotonic() - started)))
        check_cancelled(update)
        return {"command": command, "returncode": process.returncode, "stdout": "".join(logs),
                "stderr": "", "wall_clock_s": round(time.monotonic() - started, 3)}
    except BaseException:
        if update:
            update(result={"command": command, "stdout": "".join(logs)[-100000:], "partial": True,
                           "wall_clock_s": round(time.monotonic() - started, 3)})
        raise
    finally:
        if process.poll() is None:
            # Only terminate the process tree created for this accepted task.
            if os.name == "nt":
                try:
                    subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"],
                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=5,
                        creationflags=subprocess.CREATE_NO_WINDOW)
                except (OSError, subprocess.TimeoutExpired):
                    pass
            else:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            if process.poll() is None:
                process.kill()
        process.wait()
        reader.join(timeout=2)
        process.stdout.close()
