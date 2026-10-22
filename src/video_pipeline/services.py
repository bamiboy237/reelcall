"""Start the local job service for an installed copy of Reelcall."""

import hashlib
import json
import os
import secrets
import shutil
import stat
import subprocess
import sys
import time
from pathlib import Path
from tempfile import NamedTemporaryFile
from urllib.parse import quote

from filelock import FileLock
from filelock import Timeout as LockTimeout

SERVICE_FILES = ("compose.yaml", "Dockerfile", "pyproject.toml", "uv.lock", "README.md")


def command(
    arguments: list[str],
    *,
    environment: dict[str, str] | None = None,
    timeout: int = 30,
) -> str:
    try:
        result = subprocess.run(
            arguments,
            env=environment,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        raise ValueError(f"{arguments[0]} timed out. Try again.") from None
    if result.returncode:
        detail = (result.stderr or result.stdout).strip()
        for key in ("POSTGRES_PASSWORD", "HF_TOKEN"):
            secret = (environment or os.environ).get(key)
            if secret:
                detail = detail.replace(secret, "[REDACTED_SECRET]")
        raise ValueError(f"{arguments[0]} failed:\n{detail}")
    return result.stdout.strip()


def docker_ready() -> bool:
    try:
        result = subprocess.run(
            ["docker", "info", "--format", "{{.ServerVersion}}"],
            capture_output=True,
            timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return result.returncode == 0


def local_context() -> str:
    if shutil.which("docker") is None:
        raise ValueError("Install Docker, then run reelcall again.")
    command(["docker", "compose", "version"])
    context = command(["docker", "context", "show"])
    if os.environ.get("DOCKER_HOST") and not os.environ.get("DOCKER_CONTEXT"):
        endpoint = os.environ["DOCKER_HOST"]
    else:
        endpoint = command(
            [
                "docker",
                "context",
                "inspect",
                context,
                "--format",
                "{{.Endpoints.docker.Host}}",
            ]
        )
    if not endpoint.startswith(("unix://", "npipe://")):
        raise ValueError("Select a local Docker context to manage these services.")
    return context


def start_docker() -> None:
    context = local_context()
    if docker_ready():
        return
    if os.environ.get("DOCKER_HOST"):
        raise ValueError(
            "Docker is unavailable at DOCKER_HOST. Start that engine first."
        )
    if sys.platform == "darwin" and context in ("orbstack", "desktop-linux", "default"):
        app = "OrbStack" if context == "orbstack" else "Docker"
        command(["open", "-a", app])
    elif context == "desktop-linux":
        command(["docker", "desktop", "start", "--detach"])
    elif (
        sys.platform == "linux"
        and context in ("default", "rootless")
        and shutil.which("systemctl")
    ):
        command(["systemctl", "--user", "start", "docker"])
    else:
        raise ValueError(
            f"Start Docker for context {context!r}, then run reelcall again."
        )
    deadline = time.monotonic() + 120
    while time.monotonic() < deadline:
        if docker_ready():
            return
        time.sleep(0.5)
    raise ValueError("Docker did not start within two minutes. Check the Docker app.")


def state_home() -> Path:
    if value := os.environ.get("XDG_STATE_HOME"):
        return Path(value).expanduser() / "reelcall"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "Reelcall"
    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA")
        if not base:
            raise ValueError("LOCALAPPDATA is not set.")
        return Path(base) / "Reelcall"
    return Path.home() / ".local" / "state" / "reelcall"


def service_files() -> dict[str, bytes]:
    package = Path(__file__).resolve().parent
    checkout = package.parent.parent
    if (checkout / "src" / "video_pipeline").resolve() == package:
        resources = checkout
    else:
        resources = package / "_service"
    files = {}
    for name in SERVICE_FILES:
        path = resources / name
        if not path.is_file():
            raise ValueError("Service files are missing. Reinstall Reelcall.")
        files[name] = path.read_bytes()
    for path in sorted(package.rglob("*.py")):
        relative = path.relative_to(package).as_posix()
        files[f"src/video_pipeline/{relative}"] = path.read_bytes()
    return files


class LocalService:
    def __init__(self, data_dir: Path) -> None:
        self.data_dir = data_dir.expanduser().resolve()
        identity = hashlib.sha256(os.fsencode(self.data_dir)).hexdigest()[:16]
        self.project = f"reelcall-{identity}"
        self.directory = state_home() / self.project

    def password(self) -> str:
        path = self.directory / "credentials.json"
        if path.exists():
            if os.name == "posix" and stat.S_IMODE(path.stat().st_mode) & 0o077:
                raise ValueError(f"Make {path} private with chmod 600, then try again.")
            try:
                value = json.loads(path.read_text(encoding="utf-8"))["password"]
            except (ValueError, KeyError, TypeError):
                raise ValueError(
                    f"Cannot read the service password in {path}."
                ) from None
            if not isinstance(value, str) or not value:
                raise ValueError(f"The service password in {path} is invalid.")
            return value
        value = secrets.token_urlsafe(32)
        temporary = None
        try:
            with NamedTemporaryFile(
                "w", dir=self.directory, delete=False, encoding="utf-8"
            ) as stream:
                temporary = Path(stream.name)
                json.dump({"password": value}, stream)
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
            temporary.replace(path)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
        return value

    def setup(self) -> tuple[list[str], dict[str, str]]:
        files = service_files()
        digest = hashlib.sha256()
        for name, contents in sorted(files.items()):
            digest.update(name.encode() + b"\0" + contents + b"\0")
        version = digest.hexdigest()
        context = self.directory / "images" / version
        for name, contents in files.items():
            path = context / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(contents)
        environment = {
            **os.environ,
            "POSTGRES_PASSWORD": self.password(),
            "LOCAL_UID": str(os.getuid()) if hasattr(os, "getuid") else "1000",
            "LOCAL_GID": str(os.getgid()) if hasattr(os, "getgid") else "1000",
            "VIDEO_PIPELINE_DATA_DIR": str(self.data_dir),
            "VIDEO_PIPELINE_DATABASE_NAME": "video_pipeline",
            "VIDEO_PIPELINE_POSTGRES_PORT": "",
            "REELCALL_WORKER_IMAGE": f"reelcall-worker:{version}",
        }
        arguments = [
            "docker",
            "compose",
            "--project-name",
            self.project,
            "--project-directory",
            str(context),
            "--file",
            str(context / "compose.yaml"),
        ]
        return arguments, environment

    def ready(self, arguments: list[str], environment: dict[str, str]) -> bool:
        output = command(
            [*arguments, "ps", "--all", "--format", "json"], environment=environment
        )
        try:
            rows = json.loads(output) if output else []
            if isinstance(rows, dict):
                rows = [rows]
        except json.JSONDecodeError:
            rows = [json.loads(line) for line in output.splitlines()]
        for service, count in (
            ("postgres", 1),
            ("init-jobs", 1),
            ("worker", 2),
            ("search-worker", 1),
        ):
            containers = [row for row in rows if row["Service"] == service]
            if len(containers) != count:
                return False
            for container in containers:
                if service == "init-jobs":
                    if container["State"] != "exited" or container["ExitCode"] != 0:
                        return False
                elif container["State"] != "running":
                    return False
                if service == "postgres":
                    if container["Health"] != "healthy":
                        return False
                elif container["Image"] != environment["REELCALL_WORKER_IMAGE"]:
                    return False
        return True

    def start(self) -> str:
        start_docker()
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        try:
            with FileLock(self.directory / "launch.lock", timeout=1200):
                arguments, environment = self.setup()
                if not self.ready(arguments, environment):
                    cached = command(
                        [
                            "docker",
                            "image",
                            "ls",
                            "--quiet",
                            "--filter",
                            f"reference={environment['REELCALL_WORKER_IMAGE']}",
                        ]
                    )
                    command(
                        [
                            *arguments,
                            "up",
                            "--no-build" if cached else "--build",
                            "--wait",
                            "--wait-timeout",
                            "120",
                        ],
                        environment=environment,
                        timeout=1800,
                    )
                address = command(
                    [*arguments, "port", "postgres", "5432"], environment=environment
                )
                port = address.rsplit(":", 1)[-1]
                if not port.isdigit() or not 1 <= int(port) <= 65535:
                    raise ValueError("Cannot find the local job database port.")
        except LockTimeout:
            raise ValueError("Another Reelcall startup is still running.") from None
        password = quote(environment["POSTGRES_PASSWORD"], safe="")
        return f"postgresql://video_pipeline:{password}@127.0.0.1:{port}/video_pipeline"

    def stop(self) -> bool:
        if not self.directory.is_dir():
            return False
        local_context()
        if not docker_ready():
            return False
        try:
            with FileLock(self.directory / "launch.lock", timeout=30):
                arguments, environment = self.setup()
                command([*arguments, "stop"], environment=environment, timeout=120)
        except LockTimeout:
            raise ValueError(
                "Reelcall is starting. Try stop again when it finishes."
            ) from None
        return True
