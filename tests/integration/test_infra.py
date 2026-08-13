"""Infrastructure integration tests — Phase 0.

These tests talk to real Docker Compose services (Postgres, Kafka) and are
the automated half of the Phase 0 gate. They are skipped with an explicit
reason only when Docker is unavailable, never silently.

To run:  docker compose up -d   (or python -m pytest -m integration)
"""

import json
import subprocess
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent.parent

pytestmark = pytest.mark.integration


def _docker_available() -> bool:
    try:
        subprocess.run(
            ["docker", "info"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=True,
            timeout=15,
        )
        return True
    except Exception:
        return False


REQUIRE_DOCKER = pytest.mark.skipif(
    not _docker_available(),
    reason="Docker daemon not reachable — infra integration tests need Docker",
)


def _compose(args: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["docker", "compose", *args],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=180,
    )


def _service_healthy(name: str) -> bool:
    out = _compose(["ps", "--format", "json"]).stdout
    name_found = False
    for line in out.splitlines():
        try:
            info = json.loads(line)
            if info.get("Service") == name:
                name_found = True
                return info.get("Health") == "healthy"
        except json.JSONDecodeError:
            continue
    return name_found


@pytest.fixture(scope="module")
def compose_up():
    """Ensure compose is up before the module's tests; tear down after module."""
    if not _docker_available():
        pytest.skip("Docker daemon not reachable — infra integration tests need Docker")
    _compose(["up", "-d"])

    deadline = time.time() + 180
    postgres_ok = kafka_ok = False
    while time.time() < deadline:
        postgres_ok = _service_healthy("postgres")
        kafka_ok = _service_healthy("kafka")
        if postgres_ok and kafka_ok:
            break
        time.sleep(5)

    yield {"postgres": postgres_ok, "kafka": kafka_ok}
    _compose(["down"])


@REQUIRE_DOCKER
class TestComposeUp:
    def test_postgres_becomes_healthy(self, compose_up):
        assert compose_up["postgres"], "Postgres failed to reach healthy status"

    def test_kafka_becomes_healthy(self, compose_up):
        assert compose_up["kafka"], "Kafka failed to reach healthy status"


@REQUIRE_DOCKER
class TestServiceConnectivity:
    def test_postgres_port_open(self, compose_up):
        import socket

        with socket.create_connection(("localhost", 5432), timeout=5):
            pass  # port accepting connections

    def test_kafka_port_open(self, compose_up):
        import socket

        with socket.create_connection(("localhost", 9092), timeout=5):
            pass  # port accepting connections

    def test_postgres_healthcheck_via_pg_isready_style(self, compose_up):
        # Container health is the authoritative probe; verify no crash loop.
        proc = _compose(["top", "postgres"])
        assert proc.returncode == 0

    def test_kafka_produces_no_container_errors(self, compose_up):
        logs = _compose(["logs", "--tail=50", "kafka"]).stdout.lower()
        assert "fatal" not in logs, "kafka logs show fatal errors"