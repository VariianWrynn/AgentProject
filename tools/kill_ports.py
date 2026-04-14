"""
Kill processes occupying MCP (8002) and API (8003) server ports.

Usage:
    python tools/kill_ports.py              # kill both 8002 and 8003
    python tools/kill_ports.py 8002         # kill only 8002
    python tools/kill_ports.py 8002 8003    # explicit list
"""

import platform
import subprocess
import sys


def kill_port(port: int) -> bool:
    """Kill any process listening on *port*. Returns True if a process was killed."""
    system = platform.system()

    if system == "Windows":
        try:
            out = subprocess.check_output(
                ["powershell", "-Command",
                 f"(Get-NetTCPConnection -LocalPort {port} -ErrorAction SilentlyContinue)"
                 f".OwningProcess"],
                text=True, stderr=subprocess.DEVNULL,
            ).strip()
        except subprocess.CalledProcessError:
            print(f"  Port {port}: not in use")
            return False

        pids = {int(p) for p in out.splitlines() if p.strip().isdigit() and int(p) > 0}
        if not pids:
            print(f"  Port {port}: not in use")
            return False

        for pid in pids:
            try:
                subprocess.run(
                    ["powershell", "-Command", f"Stop-Process -Id {pid} -Force"],
                    check=True, stderr=subprocess.DEVNULL,
                )
                print(f"  Port {port}: killed PID {pid}")
            except subprocess.CalledProcessError:
                print(f"  Port {port}: failed to kill PID {pid}")
                return False
        return True

    else:  # Linux / macOS
        try:
            out = subprocess.check_output(
                ["lsof", "-ti", f"tcp:{port}"],
                text=True, stderr=subprocess.DEVNULL,
            ).strip()
        except subprocess.CalledProcessError:
            print(f"  Port {port}: not in use")
            return False

        pids = {int(p) for p in out.splitlines() if p.strip().isdigit()}
        if not pids:
            print(f"  Port {port}: not in use")
            return False

        for pid in pids:
            try:
                subprocess.run(["kill", "-9", str(pid)], check=True)
                print(f"  Port {port}: killed PID {pid}")
            except subprocess.CalledProcessError:
                print(f"  Port {port}: failed to kill PID {pid}")
                return False
        return True


def main():
    ports = [int(p) for p in sys.argv[1:]] if len(sys.argv) > 1 else [8002, 8003]
    print(f"Killing processes on ports: {ports}")
    killed = 0
    for port in ports:
        if kill_port(port):
            killed += 1
    print(f"Done. {killed}/{len(ports)} ports freed.")


if __name__ == "__main__":
    main()
