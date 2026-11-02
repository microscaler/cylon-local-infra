from __future__ import annotations

import io
import json
import unittest
from pathlib import Path
from unittest import mock

import massive_flatfile_mirror as mirror


class MemorySource:
    def __init__(
        self,
        prefixes: dict[str, list[mirror.ObjectHead]] | None = None,
        denied: set[str] | None = None,
        bodies: dict[str, bytes] | None = None,
    ) -> None:
        self.prefixes = prefixes or {}
        self.denied = denied or set()
        self.bodies = bodies or {}

    def list_prefixes(self) -> list[str]:
        return sorted(set(self.prefixes) | self.denied)

    def list_objects(self, prefix: str) -> list[mirror.ObjectHead]:
        if prefix in self.denied:
            raise mirror.PrefixDenied(prefix)
        return list(self.prefixes.get(prefix, []))

    def open(self, key: str) -> io.BytesIO:
        return io.BytesIO(self.bodies[key])


class MemoryDest:
    def __init__(self) -> None:
        self.blobs: dict[str, bytes] = {}
        self.meta: dict[str, tuple[int, str]] = {}

    def exists_matching(self, blob: str, size: int, etag: str) -> bool:
        return self.meta.get(blob) == (size, etag)

    def put(self, blob: str, stream, size: int, etag: str) -> None:
        data = stream.read()
        self.blobs[blob] = data
        self.meta[blob] = (size, etag)


class MemorySecrets:
    def __init__(self, values: dict[str, str]) -> None:
        self.values = values
        self.gets: list[str] = []

    def get(self, name: str) -> str:
        self.gets.append(name)
        if name not in self.values:
            raise mirror.SecretNotFound(name)
        return self.values[name]


class ConfigTests(unittest.TestCase):
    def test_load_config_reads_secret_manager_not_env(self) -> None:
        secrets = MemorySecrets(
            {
                "MASSIVE_S3_ACCESS_KEY": "AKIAEXAMPLE",
                "MASSIVE_S3_SECRET_KEY": "secret",
                "MASSIVE_S3_ENDPOINT": "https://files.massive.com",
                "MASSIVE_BUCKET": "flatfiles",
            }
        )
        cfg = mirror.load_config(
            {
                "MASSIVE_S3_ACCESS_KEY": "from-disk-must-be-ignored",
                "MASSIVE_S3_SECRET_KEY": "from-disk-must-be-ignored",
                "MASSIVE_MIRROR_GCS_BUCKET": "microscaler-massive",
                "GOOGLE_CLOUD_PROJECT": "microscaler",
            },
            secrets=secrets,
        )
        self.assertEqual(cfg.access_key, "AKIAEXAMPLE")
        self.assertEqual(cfg.secret_key, "secret")
        self.assertEqual(cfg.source_bucket, "flatfiles")
        self.assertEqual(cfg.endpoint, "https://files.massive.com")
        self.assertEqual(cfg.dest_bucket, "microscaler-massive")
        self.assertEqual(cfg.dest_prefix, "raw")
        self.assertEqual(cfg.secret_project, "microscaler")

    def test_load_config_requires_secret_manager_s3_keys(self) -> None:
        with self.assertRaises(mirror.ConfigError):
            mirror.load_config(
                {"GOOGLE_CLOUD_PROJECT": "microscaler"},
                secrets=MemorySecrets({}),
            )

    def test_gcs_blob_stays_under_raw_not_parquet(self) -> None:
        blob = mirror.gcs_blob_name("raw", "us_stocks_sip/day_aggs_v1/2024/01/2024-01-02.csv.gz")
        self.assertEqual(blob, "raw/us_stocks_sip/day_aggs_v1/2024/01/2024-01-02.csv.gz")
        self.assertFalse(blob.startswith("parquet/"))


class SyncTests(unittest.TestCase):
    def test_copies_missing_objects_and_skips_matching_etag(self) -> None:
        head = mirror.ObjectHead(
            key="us_stocks_sip/day_aggs_v1/2024/01/2024-01-02.csv.gz",
            size=4,
            etag="abc",
        )
        source = MemorySource(
            prefixes={"us_stocks_sip/": [head]},
            bodies={head.key: b"ohlc"},
        )
        dest = MemoryDest()
        dest.meta["raw/" + head.key] = (4, "abc")
        new_head = mirror.ObjectHead(
            key="us_stocks_sip/minute_aggs_v1/2024/01/2024-01-02.csv.gz",
            size=3,
            etag="def",
        )
        source.prefixes["us_stocks_sip/"].append(new_head)
        source.bodies[new_head.key] = b"min"

        result = mirror.sync_all(source, dest, dest_prefix="raw")
        self.assertEqual(result.copied, 1)
        self.assertEqual(result.skipped, 1)
        self.assertEqual(dest.blobs["raw/" + new_head.key], b"min")
        self.assertNotIn("raw/" + head.key, dest.blobs)

    def test_denied_prefix_is_recorded_and_does_not_abort(self) -> None:
        allowed = mirror.ObjectHead(
            key="us_stocks_sip/day_aggs_v1/2024/01/2024-01-02.csv.gz",
            size=4,
            etag="abc",
        )
        source = MemorySource(
            prefixes={"us_stocks_sip/": [allowed]},
            denied={"us_options_opra/"},
            bodies={allowed.key: b"ohlc"},
        )
        dest = MemoryDest()
        result = mirror.sync_all(source, dest, dest_prefix="raw")
        self.assertEqual(result.copied, 1)
        self.assertEqual(result.denied_prefixes, ["us_options_opra/"])

    def test_status_payload_redacts_secrets(self) -> None:
        cfg = mirror.load_config(
            {"GOOGLE_CLOUD_PROJECT": "microscaler"},
            secrets=MemorySecrets(
                {
                    "MASSIVE_S3_ACCESS_KEY": "AKIAEXAMPLE",
                    "MASSIVE_S3_SECRET_KEY": "super-secret",
                    "MASSIVE_S3_ENDPOINT": "https://files.massive.com",
                    "MASSIVE_BUCKET": "flatfiles",
                }
            ),
        )
        payload = json.dumps(mirror.status_dict(cfg, mirror.SyncResult()))
        self.assertNotIn("super-secret", payload)
        self.assertNotIn("AKIAEXAMPLE", payload)
        self.assertIn("flatfiles", payload)
        self.assertIn("raw", payload)


class SecretManagerTests(unittest.TestCase):
    def test_gcloud_access_reads_stdout_and_never_writes_a_file(self) -> None:
        writes: list[str] = []

        def fake_run(argv, **kwargs):
            self.assertEqual(argv[:4], ["gcloud", "secrets", "versions", "access"])
            self.assertIn("--secret=MASSIVE_S3_ACCESS_KEY", argv)
            self.assertIn("--project=microscaler", argv)
            self.assertNotIn("--data-file", " ".join(argv))
            self.assertTrue(kwargs.get("capture_output"))
            self.assertNotIn("stdout", kwargs)
            if "stdin" in kwargs:
                writes.append("stdin")
            return mock.Mock(stdout="AKIAFROMGSM\n", returncode=0)

        store = mirror.GcloudSecretManager(
            project="microscaler",
            gcloud_bin=Path("gcloud"),
        )
        with mock.patch.object(mirror.subprocess, "run", side_effect=fake_run):
            value = store.get("MASSIVE_S3_ACCESS_KEY")
        self.assertEqual(value, "AKIAFROMGSM")
        self.assertEqual(writes, [])


class SystemdTests(unittest.TestCase):
    def test_units_run_raw_sync_on_the_gce_downloader(self) -> None:
        service, timer = mirror.render_systemd_units("casibbald")
        self.assertIn("ExecStart=/opt/massive-flatfile-mirror/massive_flatfile_mirror.py sync", service)
        self.assertIn("GOOGLE_CLOUD_PROJECT=microscaler", service)
        self.assertNotIn("EnvironmentFile", service)
        self.assertNotIn("/etc/massive-flatfile-mirror.env", service)
        self.assertNotIn("parquet", service)
        self.assertIn("OnUnitInactiveSec=6h", timer)


class JustfileTests(unittest.TestCase):
    def test_justfile_exposes_iap_mirror_recipes(self) -> None:
        just = Path(__file__).resolve().parents[1] / "justfile"
        text = just.read_text()
        for name in (
            "massive-mirror-status",
            "massive-mirror-list",
            "massive-mirror-sync",
            "massive-mirror-logs",
        ):
            self.assertIn(name, text)
        self.assertIn("massive_flatfile_mirror.py", text)
        self.assertNotIn("parquet", text.split("massive-mirror-sync:")[1].split("# ──")[0])
        self.assertNotIn("/etc/massive-flatfile-mirror.env", text)


class CliTests(unittest.TestCase):
    def test_parser_exposes_list_sync_status_install(self) -> None:
        parser = mirror.build_parser()
        self.assertEqual(
            {action.dest for action in parser._subparsers._group_actions[0]._choices_actions},
            {"list", "sync", "status", "install"},
        )


if __name__ == "__main__":
    unittest.main()
