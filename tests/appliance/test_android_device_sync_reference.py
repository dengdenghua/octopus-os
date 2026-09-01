"""Keep the Android reference integration aligned with device-sync v1."""

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MOBILE_DOCS = ROOT / "docs/mobile"
REFERENCE = MOBILE_DOCS / "android-reference"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_android_client_matches_the_machine_readable_contract() -> None:
    contract = json.loads(_read(MOBILE_DOCS / "device-sync-contract.json"))
    client = _read(REFERENCE / "DeviceSyncClient.kt")
    headers = contract["authentication"]["headers"]

    assert f"const val PROTOCOL_VERSION = {contract['protocolVersion']}" in client
    assert (
        f'const val VERSION_HEADER = "{next(key for key in headers if "Version" in key)}"' in client
    )
    assert (
        f'const val DEVICE_ID_HEADER = "{next(key for key in headers if "Device-ID" in key)}"'
        in client
    )
    assert (
        f'const val OFFSET_HEADER = "{contract["endpoints"]["appendChunk"]["offsetHeader"]}"'
        in client
    )
    assert "const val MAX_CHUNK_BYTES = 8 * 1024 * 1024" in client
    assert contract["capabilities"]["maxChunkBytes"] == 8 * 1024 * 1024
    assert '.header("Authorization", "EchoDevice $deviceCredential")' in client

    for endpoint in contract["endpoints"].values():
        path = endpoint["path"].split("?", 1)[0]
        stable_prefix = path.split("{", 1)[0]
        assert stable_prefix in client


def test_android_worker_reuses_mobile_identity_and_has_bounded_background_io() -> None:
    worker = _read(REFERENCE / "EchoDeviceSyncWorker.kt")

    assert "DeviceRegistration(applicationContext).deviceId" in worker
    assert "KVUtils.getEchoAuthToken()" in worker
    assert "MediaStore.Images.Media.EXTERNAL_CONTENT_URI" in worker
    assert "private const val MAX_PHOTOS_PER_RUN = 25" in worker
    assert "ContentResolver.QUERY_ARG_LIMIT" in worker
    assert "ContentResolver.QUERY_ARG_SORT_COLUMNS" in worker
    assert "ASC LIMIT" not in worker
    assert "takePersistableUriPermission" in worker
    assert "private const val MAX_SELECTED_FILES = 500" in worker
    assert "PeriodicWorkRequestBuilder<EchoDeviceSyncWorker>(15, TimeUnit.MINUTES)" in worker
    assert "ExistingPeriodicWorkPolicy.UPDATE" in worker
    assert "OneTimeWorkRequestBuilder<EchoDeviceSyncWorker>" in worker
    assert "ExistingWorkPolicy.REPLACE" in worker
    assert "BackoffPolicy.EXPONENTIAL" in worker
    assert "KEY_CERT_PIN" in worker


def test_pairing_bootstrap_validates_everything_before_persisting() -> None:
    bootstrap = _read(REFERENCE / "EchoPairingBootstrap.kt")
    tests = _read(MOBILE_DOCS / "android-reference-test/EchoPairingBootstrapTest.kt")

    assert 'invitation.scheme.equals("echo", ignoreCase = true)' in bootstrap
    assert 'invitation.host.equals("join", ignoreCase = true)' in bootstrap
    assert 'setOf("ws", "token", "sync")' in bootstrap
    assert "MobileRuntimeSecurity.assess" in bootstrap
    assert 'syncHost.endsWith(".ts.net")' in bootstrap
    parse_position = bootstrap.index("val config = parse(raw)")
    assert parse_position < bootstrap.index("KVUtils.setEchoRpcUrl", parse_position)
    assert parse_position < bootstrap.index("KVUtils.setEchoAuthToken", parse_position)
    assert parse_position < bootstrap.index(
        "EchoDeviceSyncWorker.applyPairingSyncBase", parse_position
    )
    assert tests.count("@Test") == 5


def test_android_client_behavior_tests_cover_headers_skip_resume_and_version() -> None:
    tests = _read(MOBILE_DOCS / "android-reference-test/DeviceSyncClientTest.kt")

    assert tests.count("@Test") == 6
    assert "X-Echo-Device-ID" in tests
    assert "X-Echo-Sync-Version" in tests
    assert "skipNeverReadsOrUploadsTheLocalAssetAgain" in tests
    assert "reconcilesACommittedTimedOutChunkFromTheServerOffset" in tests
    assert "rejectsAServerThatRequiresANewerProtocol" in tests
    assert "rejectsStatusForADifferentPairedDevice" in tests
    assert "rejectsNonHttpPrivateOriginsAndCredentialBearingUrls" in tests
