"""Check the rendered budget and collect actual container evidence without secrets."""
import argparse
import http.client
import json
import socket
import subprocess
import threading
import time
import urllib.request
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

SERVICES = {"api-server", "mcp-server", "nginx", "redis", "milvus", "etcd", "minio"}
MEMORY_BUDGET = 6_500_000_000


def command(args):
    result = subprocess.run(args, capture_output=True, text=True, encoding="utf-8", check=True)
    return result.stdout.strip()


def validate_config(config):
    services = {name: value for name, value in config["services"].items()
                if not value.get("profiles")}
    if set(services) != SERVICES:
        raise ValueError(f"Expected production services {sorted(SERVICES)}")
    memory = sum(int(service.get("mem_limit", 0)) for service in services.values())
    cpu = sum(Decimal(str(service.get("cpus", 0))) for service in services.values())
    if any(not service.get("mem_limit") or not service.get("cpus") for service in services.values()):
        raise ValueError("Every production service needs an explicit CPU and memory limit")
    if memory > MEMORY_BUDGET or cpu > 4:
        raise ValueError(f"Aggregate caps exceed budget: {memory} bytes, {cpu} CPU")
    for name, service in services.items():
        if int(service.get("memswap_limit", 0)) != int(service["mem_limit"]):
            raise ValueError(f"{name} must disable container swap")
        ports = service.get("ports", [])
        if name != "nginx" and ports:
            raise ValueError(f"Internal service {name} publishes a host port")
        if service.get("privileged") or service.get("gpus"):
            raise ValueError(f"{name} requires privileged or GPU execution")
    ports = services["nginx"].get("ports", [])
    if len(ports) != 1 or int(ports[0]["target"]) != 8080:
        raise ValueError("Only the nginx port 8080 may be published")
    return {"services": sorted(services), "memory_limit_bytes": memory,
            "cpu_limit": float(cpu), "budget_bytes": MEMORY_BUDGET,
            "remaining_from_8gb_bytes": 8_000_000_000 - memory,
            "published_bind": ports[0].get("host_ip", "0.0.0.0"),
            "published_port": ports[0]["published"]}


def inspect_containers(compose):
    ids = command(compose + ["ps", "-q"]).splitlines()
    if len(ids) != len(SERVICES):
        raise ValueError("All seven production services must be running")
    raw = json.loads(command(["docker", "inspect", *ids]))
    result = {}
    for container in raw:
        service = container["Config"]["Labels"]["com.docker.compose.service"]
        if service not in SERVICES:
            continue
        state = container["State"]
        result[service] = {
            "id": container["Id"], "image_id": container["Image"],
            "status": state["Status"], "health": state.get("Health", {}).get("Status"),
            "oom_killed": state.get("OOMKilled"), "restart_count": container["RestartCount"],
            "memory_limit_bytes": container["HostConfig"]["Memory"],
            "memory_swap_limit_bytes": container["HostConfig"]["MemorySwap"],
            "nano_cpus": container["HostConfig"]["NanoCpus"],
        }
    if set(result) != SERVICES:
        raise ValueError("Container service set does not match production config")
    return result


def engine_memory(container_id):
    """Read exact memory for distroless services that have no cat executable."""
    host = json.loads(command(["docker", "context", "inspect", "--format",
                               "{{json .Endpoints.docker.Host}}"]))
    if host.startswith("npipe://"):
        stream = open(host.removeprefix("npipe://").replace("/", "\\"), "r+b", buffering=0)
        transport = SimpleNamespace(makefile=lambda _: stream)
        send, close = stream.write, stream.close
    elif host.startswith("unix://"):
        transport = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        transport.settimeout(15)
        transport.connect(host.removeprefix("unix://"))
        send, close = transport.sendall, transport.close
    else:
        raise ValueError("Exact memory verification requires a local Unix socket or named-pipe Docker context")
    try:
        send((f"GET /containers/{container_id}/stats?stream=false&one-shot=true HTTP/1.1\r\n"
              "Host: docker\r\nConnection: close\r\n\r\n").encode())
        response = http.client.HTTPResponse(transport)
        response.begin()
        if response.status != 200:
            raise ValueError(f"Docker stats failed with HTTP {response.status}")
        memory = json.loads(response.read())["memory_stats"]
        return memory["usage"], memory["limit"]
    finally:
        close()


def sample_cgroups(containers):
    result = {}
    for service, container in containers.items():
        paths = ["memory.current", "memory.peak", "memory.max", "memory.swap.max", "memory.events", "cpu.max"]
        try:
            lines = command(["docker", "exec", container["id"], "cat",
                             *["/sys/fs/cgroup/" + path for path in paths]]).splitlines()
            values = dict(zip(paths[:4], lines[:4]))
            values["memory.events"] = "\n".join(lines[4:-1])
            values["cpu.max"] = lines[-1]
        except subprocess.CalledProcessError:
            values = dict.fromkeys(paths)
            current, limit = engine_memory(container["id"])
            values["memory.current"] = str(current)
            values["memory.max"] = str(limit)
            values["observation_source"] = "docker_engine_api; kernel peak/events unavailable"
        result[service] = values
    return result


def wait_ready(compose, timeout):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            containers = inspect_containers(compose)
            if all(c["status"] == "running" and c["health"] == "healthy" for c in containers.values()):
                return containers
        except ValueError:
            pass
        time.sleep(2)
    raise TimeoutError("Production services did not become healthy before the deadline")


def request_json(url, payload=None, timeout=30):
    data = None if payload is None else json.dumps(payload).encode()
    request = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.load(response)


def verify_runtime(compose, base_url, timeout, question, expected, evidence):
    containers = wait_ready(compose, timeout)
    for name, container in containers.items():
        limits = expected["services"][name]
        if container["memory_limit_bytes"] != int(limits["mem_limit"]):
            raise ValueError(f"{name} actual memory limit differs from rendered config")
        if container["memory_swap_limit_bytes"] != container["memory_limit_bytes"]:
            raise ValueError(f"{name} actual swap policy differs from no-swap configuration")
        if container["nano_cpus"] != int(Decimal(str(limits["cpus"])) * 1_000_000_000):
            raise ValueError(f"{name} actual CPU limit differs from rendered config")
    before = sample_cgroups(containers)
    info = json.loads(command(["docker", "info", "--format", "{{json .}} "]))
    evidence.update({"docker_host": {"memory_bytes": info["MemTotal"], "cpu_count": info["NCPU"]},
                "containers_before": containers, "cgroups_before": before,
                "whole_host_8gb_verified": False,
                "whole_host_limit_note": "Container caps do not prove an 8GB VM or whole-host boundary.",
                "health": request_json(base_url + "/api/health"), "samples": []})
    if not all(evidence["health"].get(k) == "ok" for k in ("api", "mcp_server", "redis", "milvus")):
        raise ValueError("Public API reports an unhealthy dependency")
    query_error = None
    if question:
        stop = threading.Event()

        def sample():
            while not stop.is_set():
                try:
                    values = sample_cgroups(containers)
                except Exception as exc:
                    evidence["sampler_failure"] = f"{type(exc).__name__}: {exc}"
                    stop.set()
                    return
                current = [v.get("memory.current") for v in values.values()]
                evidence["samples"].append({"time_unix": time.time(), "cgroups": values,
                    "group_current_bytes": sum(int(v) for v in current) if all(v and v.isdigit() for v in current) else None})
                stop.wait(2)

        worker = threading.Thread(target=sample, daemon=True)
        worker.start()
        try:
            session = "deployment-" + str(time.time_ns())
            response = request_json(base_url + "/api/query", {"question": question, "session_id": session}, timeout)
            evidence["query"] = response
            if response.get("error") or response.get("status") in {"error", "failed"}:
                raise ValueError("Deployment query returned an error")
            if not (response.get("answer") or response.get("final_answer") or response.get("report")):
                raise ValueError("Deployment query returned no answer or report")
        except Exception as exc:
            query_error = exc
            evidence["query_failure"] = f"{type(exc).__name__}: {exc}"
        finally:
            stop.set()
            worker.join(timeout=30)
        if evidence.get("sampler_failure") or not evidence["samples"]:
            raise ValueError("Resource sampling failed or produced no observations")
    evidence["containers_after"] = inspect_containers(compose)
    evidence["cgroups_after"] = sample_cgroups(evidence["containers_after"])
    evidence["sampled_group_peak_bytes"] = max(
        (sample["group_current_bytes"] for sample in evidence["samples"] if sample["group_current_bytes"] is not None),
        default=None)
    evidence["sample_note"] = "Sampled memory.current includes cgroup cache; memory.peak is per container, not a simultaneous whole-group peak."
    for name, after in evidence["containers_after"].items():
        if after["oom_killed"] or after["restart_count"] != containers[name]["restart_count"]:
            raise ValueError(f"{name} OOM or restart occurred")
        events = evidence["cgroups_after"][name].get("memory.events")
        if events and any(int(line.split()[1]) for line in events.splitlines() if line.split()[0] in {"oom", "oom_kill"}):
            raise ValueError(f"{name} cgroup reported OOM")
    if query_error:
        raise query_error
    return evidence


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", default=".env.deploy.example")
    parser.add_argument("--compose-file", default="compose.yaml")
    parser.add_argument("--runtime", action="store_true")
    parser.add_argument("--base-url", default="http://127.0.0.1:8080")
    parser.add_argument("--timeout", type=int, default=1800)
    parser.add_argument("--question", help="Optional real query sent through the gateway")
    parser.add_argument("--output", default="test_output/deployment-verification.json")
    args = parser.parse_args()
    compose = ["docker", "compose", "-f", args.compose_file, "--env-file", args.env_file]
    config = json.loads(command(compose + ["config", "--format", "json"]))
    evidence = {"config": validate_config(config), "runtime_executed": args.runtime}
    failed = False
    if args.runtime:
        evidence["runtime"] = {}
        try:
            verify_runtime(compose, args.base_url.rstrip("/"), args.timeout, args.question, config, evidence["runtime"])
            evidence["runtime"]["verification_passed"] = True
        except Exception as exc:
            evidence["runtime"]["verification_passed"] = False
            evidence["runtime"]["failure"] = f"{type(exc).__name__}: {exc}"
            failed = True
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(evidence, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps({"output": str(output), **evidence["config"], "runtime_executed": args.runtime}))
    if failed:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
