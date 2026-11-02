#!/usr/bin/env python3
"""Mirror Massive Flat Files (raw) into GCS. Parquet transform is a later step."""
from __future__ import annotations

import argparse
import dataclasses
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import BinaryIO, Iterable, Protocol, Sequence


DEFAULT_ENDPOINT = "https://files.massive.com"
DEFAULT_SOURCE_BUCKET = "flatfiles"
DEFAULT_DEST_BUCKET = "microscaler-massive"
DEFAULT_DEST_PREFIX = "raw"
DEFAULT_GCLOUD = "/snap/bin/gcloud"
DEFAULT_SECRET_PROJECT = "microscaler"
REQUIRED_SECRETS = ("MASSIVE_S3_ACCESS_KEY", "MASSIVE_S3_SECRET_KEY")


class ConfigError(ValueError):
    pass


class SecretNotFound(ConfigError):
    def __init__(self, name: str) -> None:
        super().__init__(f"secret not found: {name}")
        self.name = name


class PrefixDenied(PermissionError):
    def __init__(self, prefix: str) -> None:
        super().__init__(f"denied prefix: {prefix}")
        self.prefix = prefix


@dataclasses.dataclass(frozen=True)
class ObjectHead:
    key: str
    size: int
    etag: str


@dataclasses.dataclass(frozen=True)
class MirrorConfig:
    access_key: str
    secret_key: str
    endpoint: str
    source_bucket: str
    dest_bucket: str
    dest_prefix: str
    gcloud_bin: Path
    state_dir: Path
    secret_project: str


@dataclasses.dataclass
class SyncResult:
    copied: int = 0
    skipped: int = 0
    denied_prefixes: list[str] = dataclasses.field(default_factory=list)
    failed: int = 0

    def as_dict(self) -> dict[str, object]:
        return {
            "copied": self.copied,
            "skipped": self.skipped,
            "denied_prefixes": list(self.denied_prefixes),
            "failed": self.failed,
        }


class SourceStore(Protocol):
    def list_prefixes(self) -> list[str]: ...
    def list_objects(self, prefix: str) -> Iterable[ObjectHead]: ...
    def open(self, key: str) -> BinaryIO: ...


class DestStore(Protocol):
    def exists_matching(self, blob: str, size: int, etag: str) -> bool: ...
    def put(self, blob: str, stream: BinaryIO, size: int, etag: str) -> None: ...


class SecretStore(Protocol):
    def get(self, name: str) -> str: ...


class GcloudSecretManager:
    """Read Secret Manager payloads from stdout only. Never write them to disk."""

    def __init__(self, project: str, gcloud_bin: Path, version: str = "latest") -> None:
        self.project = project
        self.gcloud_bin = gcloud_bin
        self.version = version

    def get(self, name: str) -> str:
        argv = [
            str(self.gcloud_bin),
            "secrets",
            "versions",
            "access",
            self.version,
            f"--secret={name}",
            f"--project={self.project}",
        ]
        try:
            proc = subprocess.run(
                argv,
                check=True,
                capture_output=True,
                text=True,
            )
        except subprocess.CalledProcessError as exc:
            raise SecretNotFound(name) from exc
        value = proc.stdout.rstrip("\n")
        if not value:
            raise SecretNotFound(name)
        return value


def _secret_or_default(store: SecretStore, name: str, default: str) -> str:
    try:
        value = store.get(name).strip()
    except SecretNotFound:
        return default
    return value or default


def load_config(
    env: dict[str, str] | None = None,
    *,
    secrets: SecretStore | None = None,
) -> MirrorConfig:
    values = env if env is not None else dict(os.environ)
    project = (
        values.get("MASSIVE_MIRROR_GCP_PROJECT")
        or values.get("GOOGLE_CLOUD_PROJECT")
        or DEFAULT_SECRET_PROJECT
    ).strip()
    gcloud_bin = Path(values.get("MASSIVE_MIRROR_GCLOUD_BIN", DEFAULT_GCLOUD))
    store = secrets or GcloudSecretManager(project=project, gcloud_bin=gcloud_bin)
    required: dict[str, str] = {}
    try:
        for name in REQUIRED_SECRETS:
            required[name] = store.get(name).strip()
    except SecretNotFound as exc:
        raise ConfigError(
            "MASSIVE_S3_ACCESS_KEY and MASSIVE_S3_SECRET_KEY must come from "
            f"Secret Manager in {project}"
        ) from exc
    if not all(required.values()):
        raise ConfigError(
            "MASSIVE_S3_ACCESS_KEY and MASSIVE_S3_SECRET_KEY must come from "
            f"Secret Manager in {project}"
        )
    access = required["MASSIVE_S3_ACCESS_KEY"]
    secret = required["MASSIVE_S3_SECRET_KEY"]
    return MirrorConfig(
        access_key=access,
        secret_key=secret,
        endpoint=_secret_or_default(store, "MASSIVE_S3_ENDPOINT", DEFAULT_ENDPOINT),
        source_bucket=_secret_or_default(store, "MASSIVE_BUCKET", DEFAULT_SOURCE_BUCKET),
        dest_bucket=values.get("MASSIVE_MIRROR_GCS_BUCKET", DEFAULT_DEST_BUCKET).strip()
        or DEFAULT_DEST_BUCKET,
        dest_prefix=values.get("MASSIVE_MIRROR_GCS_PREFIX", DEFAULT_DEST_PREFIX).strip()
        or DEFAULT_DEST_PREFIX,
        gcloud_bin=gcloud_bin,
        state_dir=Path(
            values.get(
                "MASSIVE_MIRROR_STATE_DIR",
                "/var/lib/massive-flatfile-mirror",
            )
        ),
        secret_project=project,
    )


def gcs_blob_name(dest_prefix: str, key: str) -> str:
    prefix = dest_prefix.strip("/")
    return f"{prefix}/{key.lstrip('/')}"


def sync_all(
    source: SourceStore,
    dest: DestStore,
    *,
    dest_prefix: str = DEFAULT_DEST_PREFIX,
) -> SyncResult:
    result = SyncResult()
    for prefix in source.list_prefixes():
        try:
            objects = list(source.list_objects(prefix))
        except PrefixDenied as exc:
            result.denied_prefixes.append(exc.prefix)
            continue
        for obj in objects:
            blob = gcs_blob_name(dest_prefix, obj.key)
            if dest.exists_matching(blob, obj.size, obj.etag):
                result.skipped += 1
                continue
            with source.open(obj.key) as stream:
                dest.put(blob, stream, obj.size, obj.etag)
            result.copied += 1
    result.denied_prefixes.sort()
    return result


def status_dict(cfg: MirrorConfig, result: SyncResult | None = None) -> dict[str, object]:
    payload: dict[str, object] = {
        "endpoint": cfg.endpoint,
        "source_bucket": cfg.source_bucket,
        "dest_bucket": cfg.dest_bucket,
        "dest_prefix": cfg.dest_prefix,
        "phase": "raw",
        "secret_source": "gcp-secret-manager",
        "secret_project": cfg.secret_project,
        "has_s3_credentials": bool(cfg.access_key and cfg.secret_key),
    }
    if result is not None:
        payload.update(result.as_dict())
    return payload


def render_systemd_units(user: str) -> tuple[str, str]:
    service = f"""[Unit]
Description=Mirror Massive Flat Files (raw) into GCS
Wants=network-online.target
After=network-online.target

[Service]
Type=oneshot
User={user}
Group={user}
Environment=GOOGLE_CLOUD_PROJECT=microscaler
ExecStart=/opt/massive-flatfile-mirror/massive_flatfile_mirror.py sync
Nice=5
"""
    timer = """[Unit]
Description=Resume Massive Flat File raw mirror every 6 hours

[Timer]
OnBootSec=5min
OnUnitInactiveSec=6h
AccuracySec=5min
Persistent=true

[Install]
WantedBy=timers.target
"""
    return service, timer


def install_systemd(user: str) -> None:
    if os.geteuid() != 0:
        raise PermissionError("The install command must run as root")
    service, timer = render_systemd_units(user)
    Path("/etc/systemd/system/massive-flatfile-mirror.service").write_text(service)
    Path("/etc/systemd/system/massive-flatfile-mirror.timer").write_text(timer)
    subprocess.run(["systemctl", "daemon-reload"], check=True)
    subprocess.run(
        ["systemctl", "enable", "--now", "massive-flatfile-mirror.timer"],
        check=True,
    )


class BotoSource:
    def __init__(self, cfg: MirrorConfig) -> None:
        self.cfg = cfg
        self._client = None

    def _s3(self):
        if self._client is None:
            import boto3
            from botocore.config import Config

            session = boto3.Session(
                aws_access_key_id=self.cfg.access_key,
                aws_secret_access_key=self.cfg.secret_key,
            )
            self._client = session.client(
                "s3",
                endpoint_url=self.cfg.endpoint,
                config=Config(signature_version="s3v4"),
            )
        return self._client

    def list_prefixes(self) -> list[str]:
        prefixes: list[str] = []
        paginator = self._s3().get_paginator("list_objects_v2")
        for page in paginator.paginate(
            Bucket=self.cfg.source_bucket, Delimiter="/"
        ):
            for item in page.get("CommonPrefixes") or []:
                prefix = item.get("Prefix")
                if prefix:
                    prefixes.append(prefix)
        return prefixes

    def list_objects(self, prefix: str) -> list[ObjectHead]:
        from botocore.exceptions import ClientError

        objects: list[ObjectHead] = []
        paginator = self._s3().get_paginator("list_objects_v2")
        try:
            for page in paginator.paginate(
                Bucket=self.cfg.source_bucket, Prefix=prefix
            ):
                for item in page.get("Contents") or []:
                    key = item.get("Key")
                    if not key or key.endswith("/"):
                        continue
                    objects.append(
                        ObjectHead(
                            key=key,
                            size=int(item.get("Size") or 0),
                            etag=str(item.get("ETag") or "").strip('"'),
                        )
                    )
        except ClientError as exc:
            code = (exc.response.get("Error") or {}).get("Code", "")
            if code in {"AccessDenied", "403", "AllAccessDisabled"}:
                raise PrefixDenied(prefix) from exc
            raise
        return objects

    def open(self, key: str) -> BinaryIO:
        response = self._s3().get_object(Bucket=self.cfg.source_bucket, Key=key)
        return response["Body"]


class GcloudDest:
    def __init__(self, cfg: MirrorConfig) -> None:
        self.cfg = cfg
        self.state_path = cfg.state_dir / "copied.json"
        self._copied = self._load()

    def _load(self) -> dict[str, dict[str, object]]:
        if not self.state_path.exists():
            return {}
        return json.loads(self.state_path.read_text())

    def _save(self) -> None:
        self.cfg.state_dir.mkdir(parents=True, exist_ok=True)
        self.state_path.write_text(json.dumps(self._copied, indent=2, sort_keys=True))

    def exists_matching(self, blob: str, size: int, etag: str) -> bool:
        record = self._copied.get(blob)
        if record and int(record.get("size") or 0) == size and record.get("etag") == etag:
            return True
        return False

    def put(self, blob: str, stream: BinaryIO, size: int, etag: str) -> None:
        uri = f"gs://{self.cfg.dest_bucket}/{blob}"
        subprocess.run(
            [str(self.cfg.gcloud_bin), "storage", "cp", "-", uri],
            check=True,
            stdin=stream,
        )
        self._copied[blob] = {"size": size, "etag": etag}
        self._save()


def _list_command(cfg: MirrorConfig) -> int:
    source = BotoSource(cfg)
    accessible: list[str] = []
    denied: list[str] = []
    for prefix in source.list_prefixes():
        try:
            next(iter(source.list_objects(prefix)), None)
        except PrefixDenied:
            denied.append(prefix)
        else:
            accessible.append(prefix)
    print(
        json.dumps(
            {
                "accessible": accessible,
                "denied": denied,
                "dest": f"gs://{cfg.dest_bucket}/{cfg.dest_prefix}/",
            },
            indent=2,
        )
    )
    return 0


def _sync_command(cfg: MirrorConfig) -> int:
    result = sync_all(BotoSource(cfg), GcloudDest(cfg), dest_prefix=cfg.dest_prefix)
    print(json.dumps(status_dict(cfg, result), indent=2, sort_keys=True))
    return 0 if result.failed == 0 else 1


def _status_command(cfg: MirrorConfig) -> int:
    print(json.dumps(status_dict(cfg), indent=2, sort_keys=True))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("list", help="List accessible vs denied source prefixes")
    subparsers.add_parser("sync", help="Copy raw Flat Files into GCS")
    subparsers.add_parser("status", help="Print redacted config (no secrets)")
    install = subparsers.add_parser("install", help="Install systemd timer")
    install.add_argument("--user", required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "install":
        install_systemd(args.user)
        return 0
    cfg = load_config()
    if args.command == "list":
        return _list_command(cfg)
    if args.command == "sync":
        return _sync_command(cfg)
    if args.command == "status":
        return _status_command(cfg)
    raise AssertionError(f"Unhandled command: {args.command}")


if __name__ == "__main__":
    sys.exit(main())
