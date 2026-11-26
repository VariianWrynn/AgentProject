"""Run a verification command inside a four-CPU Linux VM with an 8GB whole-guest bound."""

import argparse
import json
import os
from pathlib import Path
import platform
import signal
import subprocess
import threading
import time


def snapshot(proc=Path("/proc")):
    memory = {key: int(value.split()[0]) * 1024
              for key, value in (line.split(":", 1) for line in (proc / "meminfo").read_text().splitlines())
              if key in {"MemTotal", "MemAvailable", "SwapTotal", "SwapFree"}}
    vmstat = dict(line.split() for line in (proc / "vmstat").read_text().splitlines())
    return {"timestamp": time.time(), **memory, "oom_kill": int(vmstat["oom_kill"]),
            "loadavg": (proc / "loadavg").read_text().strip()}


def validate_host(state, cpu_count, kernel):
    if "microsoft" in kernel.lower():
        raise ValueError("Run inside the independent guest, not the outer WSL VM")
    if cpu_count != 4:
        raise ValueError(f"Expected four guest CPUs, found {cpu_count}")
    if not 0 < state["MemTotal"] <= 8_000_000_000:
        raise ValueError("Whole-guest RAM exceeds the 8,000,000,000-byte boundary")
    if state["SwapTotal"] != 0:
        raise ValueError("Whole-guest swap must be disabled")


def run_command(command, timeout):
    process = subprocess.Popen(command, start_new_session=True)
    try:
        return process.wait(timeout=timeout)
    finally:
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGKILL)
            process.wait()
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--timeout", type=int, default=1800)
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    command = args.command[1:] if args.command[:1] == ["--"] else args.command
    if not command:
        parser.error("A verification command is required after --")
    record = {"passed": False, "kernel": platform.release(), "cpus": os.cpu_count(), "samples": []}
    stop = threading.Event()

    def sample():
        try:
            while not stop.wait(1):
                record["samples"].append(snapshot())
        except Exception as exc:
            record["sampler_error"] = str(exc)

    try:
        before = snapshot()
        record["before"] = before
        validate_host(before, record["cpus"], record["kernel"])
        record["virtualization"] = subprocess.check_output(["systemd-detect-virt"], text=True).strip()
        if record["virtualization"] not in {"kvm", "qemu"}:
            raise ValueError("Expected an independent QEMU/KVM guest")
        worker = threading.Thread(target=sample, daemon=True)
        worker.start()
        try:
            record["command_exit"] = run_command(command, args.timeout)
        finally:
            stop.set()
            worker.join(timeout=5)
        after = snapshot()
        record["after"] = after
        readings = [before, *record["samples"], after]
        record["minimum_available_bytes"] = min(row["MemAvailable"] for row in readings)
        record["maximum_unavailable_bytes"] = max(row["MemTotal"] - row["MemAvailable"] for row in readings)
        record["measurement_note"] = "Whole-guest MemTotal minus MemAvailable includes the OS and Docker; it is not a sum of container RSS."
        if record.get("sampler_error") or not record["samples"]:
            raise ValueError("Whole-guest sampler failed or produced no periodic samples")
        if after["oom_kill"] != before["oom_kill"]:
            raise ValueError("Guest kernel recorded an OOM kill")
        if record["minimum_available_bytes"] < 1_073_741_824:
            raise ValueError("Less than 1GiB of whole-guest memory remained available")
        if record["command_exit"]:
            raise ValueError("Verification command failed")
        record["passed"] = True
    except Exception as exc:
        record["failure"] = f"{type(exc).__name__}: {exc}"
    finally:
        stop.set()
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if not record["passed"]:
        raise SystemExit(record["failure"])


if __name__ == "__main__":
    main()
