"""Tests for deploy/deploy.sh and deploy/backup.sh with fake `docker`, `curl` and `aws`.

The fakes record every call, so the tests check what the scripts would do on a server:
which tag gets started, whether health is checked through Caddy, and when a rollback
happens. Run with:  pytest deploy/tests
"""

import os
import shutil
import stat
import subprocess
from pathlib import Path

import pytest

DEPLOY_DIR = Path(__file__).resolve().parents[1]

FAKE_DOCKER = """#!/usr/bin/env bash
echo "docker $* tag=${NEXUS_IMAGE_TAG:-}" >> "$FAKE_LOG"
case " $* " in
  *" pull "*) grep -qx "$NEXUS_IMAGE_TAG" "$FAKE_DIR/missing" 2>/dev/null && exit 1 ;;
  *" up "*) echo "$NEXUS_IMAGE_TAG" > "$FAKE_DIR/running" ;;
  *" exec -T db "*) printf 'PGDMP-dump-bytes'; [ -f "$FAKE_DIR/dump-fails" ] && exit 1 ;;
  *" exec -T backend "*) printf 'tar-bytes' ;;
esac
exit 0
"""

FAKE_CURL = """#!/usr/bin/env bash
echo "curl $*" >> "$FAKE_LOG"
grep -qx "$(cat "$FAKE_DIR/running" 2>/dev/null)" "$FAKE_DIR/healthy" 2>/dev/null
"""

FAKE_AWS = """#!/usr/bin/env bash
echo "aws $* stdin=$(cat)" >> "$FAKE_LOG"
"""


def _executable(path: Path, content: str) -> None:
    path.write_text(content)
    path.chmod(path.stat().st_mode | stat.S_IXUSR)


@pytest.fixture
def server(tmp_path):
    """A copy of deploy/ with a .env, plus fake binaries first on PATH."""
    app = tmp_path / "deploy"
    app.mkdir()
    for name in ("deploy.sh", "backup.sh", "docker-compose.prod.yml"):
        shutil.copy(DEPLOY_DIR / name, app / name)
    (app / ".env").write_text("NEXUS_DOMAIN=nexus.example.com\n")
    fake = tmp_path / "fake"
    fake.mkdir()
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _executable(bin_dir / "docker", FAKE_DOCKER)
    _executable(bin_dir / "curl", FAKE_CURL)
    _executable(bin_dir / "aws", FAKE_AWS)

    class Server:
        dir = app
        log = fake / "calls.log"

        def healthy(self, *tags: str) -> None:
            (fake / "healthy").write_text("".join(f"{t}\n" for t in tags))

        def missing(self, *tags: str) -> None:
            (fake / "missing").write_text("".join(f"{t}\n" for t in tags))

        def run(self, script: str, *args: str, **env: str) -> subprocess.CompletedProcess[str]:
            environ = {
                "PATH": f"{bin_dir}:{os.environ['PATH']}",
                "HOME": str(tmp_path),
                "FAKE_LOG": str(self.log),
                "FAKE_DIR": str(fake),
                "HEALTH_TIMEOUT_SECONDS": "1",
                "HEALTH_INTERVAL_SECONDS": "0.1",
                **env,
            }
            return subprocess.run(
                ["bash", str(app / script), *args],
                env=environ,
                capture_output=True,
                text=True,
                timeout=30,
            )

        def calls(self, prefix: str) -> list[str]:
            if not self.log.exists():
                return []
            return [line for line in self.log.read_text().splitlines() if line.startswith(prefix)]

        @property
        def deployed(self) -> str | None:
            state = app / ".deployed-tag"
            return state.read_text().strip() if state.exists() else None

        @property
        def running(self) -> str | None:
            path = fake / "running"
            return path.read_text().strip() if path.exists() else None

    return Server()


def test_healthy_release_is_deployed_and_remembered(server):
    server.healthy("v1")
    result = server.run("deploy.sh", "v1")
    assert result.returncode == 0, result.stdout + result.stderr
    assert server.deployed == "v1"
    ups = server.calls("docker compose")
    assert any(" pull --quiet " in c and c.endswith("tag=v1") for c in ups)
    assert any(" up -d --no-build --remove-orphans " in c and c.endswith("tag=v1") for c in ups)


def test_health_is_checked_through_caddy_with_the_real_domain(server):
    server.healthy("v1")
    server.run("deploy.sh", "v1", DEPLOY_CURL_OPTS="-k")
    curls = server.calls("curl")
    assert any("https://nexus.example.com/api/v1/health/ready" in c for c in curls)
    assert any(c.rstrip().endswith("https://nexus.example.com/") for c in curls)
    assert all("--resolve nexus.example.com:443:127.0.0.1" in c and " -k " in c for c in curls)


def test_unhealthy_release_rolls_back_to_the_previous_one(server):
    server.healthy("v1")
    assert server.run("deploy.sh", "v1").returncode == 0

    result = server.run("deploy.sh", "v2")  # v2 never becomes healthy

    assert result.returncode == 1
    assert "rolling back to v1" in result.stdout
    assert "rolled back to v1: healthy" in result.stdout
    assert server.running == "v1"
    assert server.deployed == "v1"  # the failed tag is not remembered


def test_failed_pull_also_rolls_back(server):
    server.healthy("v1", "v2")
    server.run("deploy.sh", "v1")
    server.missing("v2")  # e.g. the image was never pushed

    result = server.run("deploy.sh", "v2")

    assert result.returncode == 1
    # The stack was never restarted with v2.
    assert not any(" up " in c and c.endswith("tag=v2") for c in server.calls("docker"))
    assert server.running == "v1"


def test_first_release_failing_has_nothing_to_roll_back_to(server):
    result = server.run("deploy.sh", "v1")
    assert result.returncode == 1
    assert "no earlier release to roll back to" in result.stdout
    assert server.deployed is None


def test_unhealthy_rollback_exits_2_for_a_person_to_look_at(server):
    server.healthy("v1")
    server.run("deploy.sh", "v1")
    server.healthy()  # now nothing is healthy, e.g. the database is down

    result = server.run("deploy.sh", "v2")

    assert result.returncode == 2
    assert "manual action needed" in result.stdout


def test_requires_a_tag(server):
    result = server.run("deploy.sh")
    assert result.returncode != 0
    assert "usage: deploy.sh" in result.stderr


def test_backup_streams_database_and_uploads_to_s3(server):
    (server.dir / ".deployed-tag").write_text("v1\n")
    result = server.run("backup.sh", "s3://nexus-backups")
    assert result.returncode == 0, result.stderr
    uploads = server.calls("aws s3 cp")
    assert len(uploads) == 2
    assert "s3://nexus-backups/db/" in uploads[0] and "stdin=PGDMP-dump-bytes" in uploads[0]
    assert "s3://nexus-backups/uploads/" in uploads[1] and "stdin=tar-bytes" in uploads[1]
    dump = next(c for c in server.calls("docker") if "exec -T db" in c)
    assert "pg_dump -U nexus -d nexusai --format=custom" in dump


def test_failed_database_dump_fails_the_backup(server):
    (server.dir / ".deployed-tag").write_text("v1\n")
    (server.log.parent / "dump-fails").touch()
    result = server.run("backup.sh", "s3://nexus-backups")
    assert result.returncode != 0
    assert "done" not in result.stdout
    removed = [c for c in server.calls("aws") if c.startswith("aws s3 rm")]
    assert len(removed) == 1 and "s3://nexus-backups/db/" in removed[0]
    # The uploads archive isn't attempted after a failed dump.
    assert not any("/uploads/" in c for c in server.calls("aws"))
