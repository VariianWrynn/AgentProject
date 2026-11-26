import pytest
import os
from pathlib import Path
import signal
import subprocess
import sys
import time

from scripts.verify_vm_host import run_command, validate_host


def test_rejects_outer_wsl_or_unbounded_host():
    valid = {"MemTotal": 7_900_000_000, "SwapTotal": 0}
    validate_host(valid, 4, "6.8.0-generic")
    for state, cpus, kernel in [
        (valid, 4, "6.6.87.2-microsoft-standard-WSL2"),
        (valid, 24, "6.8.0-generic"),
        ({**valid, "MemTotal": 8_589_934_592}, 4, "6.8.0-generic"),
        ({**valid, "SwapTotal": 1}, 4, "6.8.0-generic"),
    ]:
        with pytest.raises(ValueError):
            validate_host(state, cpus, kernel)


@pytest.mark.skipif(os.name != 'posix', reason='The VM supervisor runs on Linux')
def test_timeout_terminates_descendant_that_ignores_sigterm(tmp_path):
    marker = tmp_path / 'child.pid'
    child = ("import os,pathlib,signal,time; signal.signal(signal.SIGTERM,signal.SIG_IGN); "
             f"pathlib.Path({str(marker)!r}).write_text(str(os.getpid())); time.sleep(60)")
    parent = f"import subprocess,sys,time; subprocess.Popen([sys.executable,'-c',{child!r}]); time.sleep(60)"
    child_pid = None
    try:
        with pytest.raises(subprocess.TimeoutExpired):
            run_command([sys.executable, '-c', parent], timeout=1)
        child_pid = int(marker.read_text())
        for _ in range(20):
            status = Path(f'/proc/{child_pid}/stat')
            if not status.exists() or status.read_text().split()[2] == 'Z':
                break
            time.sleep(.05)
        else:
            pytest.fail('Timed-out verification left its descendant running')
    finally:
        if child_pid is not None:
            try:
                os.kill(child_pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
