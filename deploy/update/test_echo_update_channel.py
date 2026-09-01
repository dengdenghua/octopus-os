#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import importlib.util
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock
from urllib.parse import unquote

MODULE_PATH = Path(__file__).with_name("echo_update_channel.py")
VERIFIER_PATH = Path(__file__).with_name("verify-update-bundle.py").resolve()
SPEC = importlib.util.spec_from_file_location("echo_update_channel", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class FakeResponse:
    def __init__(
        self,
        data: bytes,
        url: str,
        *,
        status: int = 200,
        headers: dict[str, str] | None = None,
    ) -> None:
        self.stream = io.BytesIO(data)
        self.url = url
        self.status = status
        self.headers = headers or {}

    def __enter__(self) -> FakeResponse:
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def read(self, size: int = -1) -> bytes:
        return self.stream.read(size)

    def getcode(self) -> int:
        return self.status

    def geturl(self) -> str:
        return self.url


class FakeOpener:
    def __init__(self, response: FakeResponse) -> None:
        self.response = response

    def open(self, _request: object, timeout: int) -> FakeResponse:
        if timeout != MODULE.DOWNLOAD_TIMEOUT_SECONDS:
            raise AssertionError("unexpected channel timeout")
        return self.response


class EchoUpdateChannelTests(unittest.TestCase):
    version = "0.2.1"
    channel = "https://updates.example.test/echo-os/stable/x86-64"

    def source_bytes(self) -> bytes:
        return (
            json.dumps(
                {
                    "schema": 1,
                    "kind": "echo-os-source-identity",
                    "repository": "https://github.com/example/echo-os.git",
                    "commit": "a" * 40,
                    "tree": "b" * 40,
                    "commit_time": "2024-01-01T00:00:00+00:00",
                    "source_date_epoch": 1704067200,
                    "dirty": False,
                },
                sort_keys=True,
                separators=(",", ":"),
            )
            + "\n"
        ).encode()

    def channel_files(self, *, changed_source: bool = False) -> dict[str, bytes]:
        source = self.source_bytes()
        if changed_source:
            source = source.replace(b"a" * 40, b"c" * 40)
        payloads = {
            f"echo-os_{self.version}.root.11111111-2222-3333-4444-555555555555.raw.zst": b"root",
            f"echo-os_{self.version}.root-verity.66666666-7777-8888-9999-aaaaaaaaaaaa.raw.zst": b"verity",
            f"echo-os_{self.version}.root-verity-sig.bbbbbbbb-cccc-dddd-eeee-ffffffffffff.raw.zst": b"signature-partition",
            f"echo-os_{self.version}.efi": b"uki",
            "OS-SOURCE-IDENTITY.json": source,
        }
        manifest = "".join(
            f"{hashlib.sha256(contents).hexdigest()}  {name}\n"
            for name, contents in payloads.items()
        ).encode()
        return {**payloads, "SHA256SUMS": manifest, "SHA256SUMS.gpg": b"signature"}

    def downloader(
        self,
        files: dict[str, bytes],
        calls: list[str],
    ):
        def fetch(url: str, destination: Path, maximum: int, expected: str | None) -> str:
            name = unquote(url.rsplit("/", 1)[1])
            calls.append(name)
            data = files[name]
            if not 1 <= len(data) <= maximum:
                raise MODULE.ChannelError("fixture exceeds bound")
            digest = hashlib.sha256(data).hexdigest()
            if expected is not None and digest != expected:
                raise MODULE.ChannelError("downloaded update artifact has the wrong SHA-256")
            destination.write_bytes(data)
            return digest

        return fetch

    def test_channel_config_accepts_only_one_https_directory(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            config = Path(directory) / "channel"
            config.write_text(self.channel + "\n", encoding="ascii")
            self.assertEqual(MODULE.load_channel_url(config), self.channel)
            invalid = (
                "http://updates.example.test/echo",
                "https://user:secret@updates.example.test/echo",
                "https://updates.example.test/echo?track=1",
                "https://updates.example.test/echo#fragment",
                "https://updates.example.test/echo%2Fstable",
                "https://updates.example.test/echo/../stable",
                "https://updates.example.test/",
                "https://updates.example.test/echo stable",
            )
            for value in invalid:
                with self.subTest(value=value):
                    config.write_text(value + "\n", encoding="ascii")
                    with self.assertRaises(MODULE.ChannelError):
                        MODULE.load_channel_url(config)

    def test_signed_bundle_is_atomically_cached_and_reused(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            cache = root / "cache"
            cache.mkdir(mode=0o700)
            keyring = root / "keyring.gpg"
            keyring.write_bytes(b"keyring")
            files = self.channel_files()
            calls: list[str] = []
            result = MODULE.fetch_bundle(
                self.channel,
                cache,
                keyring,
                VERIFIER_PATH,
                root / "gpgv",
                expected_uid=os.getuid(),
                downloader=self.downloader(files, calls),
                signature_verifier=lambda *_args: None,
            )
            target = cache / self.version
            self.assertEqual(result["bundle"], str(target))
            self.assertEqual(oct(target.stat().st_mode & 0o777), "0o555")
            self.assertTrue(all((path.stat().st_mode & 0o777) == 0o444 for path in target.iterdir()))
            self.assertFalse(any(path.name.startswith(".incoming-") for path in cache.iterdir()))
            first_call_count = len(calls)

            reused = MODULE.fetch_bundle(
                self.channel,
                cache,
                keyring,
                VERIFIER_PATH,
                root / "gpgv",
                expected_uid=os.getuid(),
                downloader=self.downloader(files, calls),
                signature_verifier=lambda *_args: None,
            )
            self.assertEqual(reused, result)
            self.assertEqual(calls[first_call_count:], ["SHA256SUMS", "SHA256SUMS.gpg"])

    def test_signature_failure_fetches_no_payload_and_cleans_staging(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            cache = root / "cache"
            cache.mkdir(mode=0o700)
            keyring = root / "keyring.gpg"
            keyring.write_bytes(b"keyring")
            calls: list[str] = []

            def reject(*_args: object) -> None:
                raise MODULE.ChannelError("untrusted signature")

            with self.assertRaisesRegex(MODULE.ChannelError, "untrusted"):
                MODULE.fetch_bundle(
                    self.channel,
                    cache,
                    keyring,
                    VERIFIER_PATH,
                    root / "gpgv",
                    expected_uid=os.getuid(),
                    downloader=self.downloader(self.channel_files(), calls),
                    signature_verifier=reject,
                )
            self.assertEqual(calls, ["SHA256SUMS", "SHA256SUMS.gpg"])
            self.assertEqual(list(cache.iterdir()), [])

    def test_abandoned_staging_is_removed_before_a_new_fetch(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            cache = root / "cache"
            cache.mkdir(mode=0o700)
            abandoned = cache / ".incoming-interrupted"
            abandoned.mkdir()
            (abandoned / "partial-root").write_bytes(b"partial")
            keyring = root / "keyring.gpg"
            keyring.write_bytes(b"keyring")

            MODULE.fetch_bundle(
                self.channel,
                cache,
                keyring,
                VERIFIER_PATH,
                root / "gpgv",
                expected_uid=os.getuid(),
                downloader=self.downloader(self.channel_files(), []),
                signature_verifier=lambda *_args: None,
            )

            self.assertFalse(abandoned.exists())
            self.assertFalse(any(child.name.startswith(".incoming-") for child in cache.iterdir()))

    def test_cache_retains_target_and_only_one_previous_version(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            cache = root / "cache"
            cache.mkdir(mode=0o700)
            for index, version in enumerate(("0.1.0", "0.2.0"), 1):
                historical = cache / version
                historical.mkdir(mode=0o555)
                os.utime(historical, ns=(index, index))
            keyring = root / "keyring.gpg"
            keyring.write_bytes(b"keyring")

            MODULE.fetch_bundle(
                self.channel,
                cache,
                keyring,
                VERIFIER_PATH,
                root / "gpgv",
                expected_uid=os.getuid(),
                downloader=self.downloader(self.channel_files(), []),
                signature_verifier=lambda *_args: None,
            )

            self.assertEqual(
                {child.name for child in cache.iterdir()},
                {"0.2.0", self.version},
            )

    def test_payload_mismatch_or_same_version_replacement_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            cache = root / "cache"
            cache.mkdir(mode=0o700)
            keyring = root / "keyring.gpg"
            keyring.write_bytes(b"keyring")
            files = self.channel_files()
            root_name = next(name for name in files if ".root." in name)
            files[root_name] = b"tampered"
            with self.assertRaisesRegex(MODULE.ChannelError, "wrong SHA-256"):
                MODULE.fetch_bundle(
                    self.channel,
                    cache,
                    keyring,
                    VERIFIER_PATH,
                    root / "gpgv",
                    expected_uid=os.getuid(),
                    downloader=self.downloader(files, []),
                    signature_verifier=lambda *_args: None,
                )
            self.assertEqual(list(cache.iterdir()), [])

            original = self.channel_files()
            MODULE.fetch_bundle(
                self.channel,
                cache,
                keyring,
                VERIFIER_PATH,
                root / "gpgv",
                expected_uid=os.getuid(),
                downloader=self.downloader(original, []),
                signature_verifier=lambda *_args: None,
            )
            replacement_calls: list[str] = []
            with self.assertRaisesRegex(MODULE.ChannelError, "immutable cached version"):
                MODULE.fetch_bundle(
                    self.channel,
                    cache,
                    keyring,
                    VERIFIER_PATH,
                    root / "gpgv",
                    expected_uid=os.getuid(),
                    downloader=self.downloader(
                        self.channel_files(changed_source=True), replacement_calls
                    ),
                    signature_verifier=lambda *_args: None,
                )
            self.assertEqual(replacement_calls, ["SHA256SUMS", "SHA256SUMS.gpg"])

    def test_download_rejects_redirect_transformation_and_oversize(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            url = f"{self.channel}/SHA256SUMS"
            cases = (
                (FakeResponse(b"data", url + "/redirected"), 4),
                (FakeResponse(b"data", url, headers={"Content-Encoding": "gzip"}), 4),
                (FakeResponse(b"12345", url, headers={"Content-Length": "5"}), 4),
                (FakeResponse(b"1234", url, headers={"Content-Length": "5"}), 6),
            )
            for index, (response, maximum) in enumerate(cases):
                with self.subTest(index=index):
                    destination = root / f"download-{index}"
                    with mock.patch.object(
                        MODULE, "create_opener", return_value=FakeOpener(response)
                    ), self.assertRaises(MODULE.ChannelError):
                        MODULE.download(url, destination, maximum)
                    self.assertFalse(destination.exists())


if __name__ == "__main__":
    unittest.main()
