#!/usr/bin/env python3
"""Run candidate-bound Paperless OCR and Office ingestion on real appliance Docker."""

from __future__ import annotations

import argparse
import hashlib
import http.client
import io
import json
import os
import re
import stat
import sys
import time
import uuid
import zipfile
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlsplit, urlunsplit

try:
    from deploy.appliance import hub_lifecycle_lab as hub_lab
except ModuleNotFoundError:
    import hub_lifecycle_lab as hub_lab

SCHEMA_VERSION = 1
PLAN_KIND = "echo.paperless-functional-physical-plan"
RESULT_KIND = "echo.paperless-functional-physical-result"
FIXTURE_KIND = "echo.paperless-functional-fixtures"
FIXTURE_MANIFEST_NAME = "paperless-fixtures.json"
APP_ID = "paperless-ngx"
MAX_FIXTURE_BYTES = 32 * 1024 * 1024
MAX_TOTAL_FIXTURE_BYTES = 64 * 1024 * 1024
MAX_RESPONSE_BYTES = 8 * 1024 * 1024
TASK_TIMEOUT_SECONDS = 20 * 60
SEARCH_TIMEOUT_SECONDS = 2 * 60
POLL_SECONDS = 2.0
SHA256 = re.compile(r"^[0-9a-f]{64}$")
SAFE_FILE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")
ASCII_TERM = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 ._-]{3,63}$")
FIXTURE_CONTRACTS: dict[str, dict[str, Any]] = {
    "ocr-zh": {
        "extension": ".pdf",
        "mediaType": "application/pdf",
        "coverage": "chinese-ocr",
    },
    "ocr-en": {
        "extension": ".pdf",
        "mediaType": "application/pdf",
        "coverage": "english-ocr",
    },
    "office-docx": {
        "extension": ".docx",
        "mediaType": ("application/vnd.openxmlformats-officedocument.wordprocessingml.document"),
        "coverage": "office-docx",
        "member": "word/document.xml",
    },
    "office-xlsx": {
        "extension": ".xlsx",
        "mediaType": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "coverage": "office-xlsx",
        "member": "xl/workbook.xml",
    },
    "office-pptx": {
        "extension": ".pptx",
        "mediaType": ("application/vnd.openxmlformats-officedocument.presentationml.presentation"),
        "coverage": "office-pptx",
        "member": "ppt/presentation.xml",
    },
}
FIXTURE_IDS = tuple(FIXTURE_CONTRACTS)


class PaperlessFunctionalLabError(RuntimeError):
    """Paperless physical functional evidence is unsafe, stale or incomplete."""


PaperlessRequest = Callable[..., tuple[int, Mapping[str, str], bytes]]


def _canonical(value: Mapping[str, Any]) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"
    ).encode()


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _strict_json_value(raw: bytes, label: str) -> Any:
    def reject_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise PaperlessFunctionalLabError(f"{label} contains duplicate JSON keys")
            result[key] = value
        return result

    try:
        return json.loads(raw.decode("utf-8"), object_pairs_hook=reject_duplicates)
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise PaperlessFunctionalLabError(f"{label} is not strict JSON") from exc


def _private_file(path: Path, label: str, *, trusted_uid: int) -> bytes:
    try:
        info = path.lstat()
    except OSError as exc:
        raise PaperlessFunctionalLabError(f"{label} is unavailable") from exc
    if (
        path.is_symlink()
        or not stat.S_ISREG(info.st_mode)
        or info.st_uid != trusted_uid
        or stat.S_IMODE(info.st_mode) != 0o400
        or not 1 <= info.st_size <= MAX_FIXTURE_BYTES
    ):
        raise PaperlessFunctionalLabError(f"{label} must be one trusted mode-0400 file")
    try:
        return hub_lab._read_regular(path, label)
    except hub_lab.HubLifecycleLabError as exc:
        raise PaperlessFunctionalLabError(str(exc)) from exc


def _fixture_directory(path: Path, *, trusted_uid: int) -> Path:
    if not path.is_absolute() or path.is_symlink():
        raise PaperlessFunctionalLabError("Paperless fixture directory must be absolute")
    try:
        root = path.resolve(strict=True)
        info = root.stat()
    except OSError as exc:
        raise PaperlessFunctionalLabError("Paperless fixture directory is unavailable") from exc
    if not root.is_dir() or info.st_uid != trusted_uid or stat.S_IMODE(info.st_mode) != 0o700:
        raise PaperlessFunctionalLabError(
            "Paperless fixture directory must be trusted and mode 0700"
        )
    return root


def _validate_office(raw: bytes, expected_member: str, label: str) -> None:
    try:
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            members = archive.infolist()
            names = {member.filename for member in members}
            total = sum(member.file_size for member in members)
            if (
                len(members) > 512
                or total > 128 * 1024 * 1024
                or expected_member not in names
                or "[Content_Types].xml" not in names
                or any(
                    member.flag_bits & 0x1
                    or member.is_dir()
                    and member.file_size != 0
                    or member.filename.startswith("/")
                    or ".." in Path(member.filename).parts
                    for member in members
                )
            ):
                raise PaperlessFunctionalLabError(f"{label} is not a bounded OOXML fixture")
    except (OSError, zipfile.BadZipFile) as exc:
        raise PaperlessFunctionalLabError(f"{label} is not valid OOXML") from exc


def _fixture_snapshot(directory: Path, *, trusted_uid: int) -> list[dict[str, Any]]:
    root = _fixture_directory(directory, trusted_uid=trusted_uid)
    manifest_raw = _private_file(
        root / FIXTURE_MANIFEST_NAME,
        "Paperless fixture manifest",
        trusted_uid=trusted_uid,
    )
    manifest = _strict_json_value(manifest_raw, "Paperless fixture manifest")
    if (
        not isinstance(manifest, dict)
        or set(manifest) != {"schemaVersion", "kind", "fixtures"}
        or manifest.get("schemaVersion") != SCHEMA_VERSION
        or manifest.get("kind") != FIXTURE_KIND
        or not isinstance(manifest.get("fixtures"), list)
        or len(manifest["fixtures"]) != len(FIXTURE_IDS)
    ):
        raise PaperlessFunctionalLabError("Paperless fixture manifest contract is invalid")
    by_id: dict[str, dict[str, Any]] = {}
    for value in manifest["fixtures"]:
        if (
            not isinstance(value, dict)
            or set(value) != {"id", "file", "searchTerm"}
            or value.get("id") not in FIXTURE_CONTRACTS
            or value["id"] in by_id
            or not isinstance(value.get("file"), str)
            or SAFE_FILE.fullmatch(value["file"]) is None
            or Path(value["file"]).suffix != FIXTURE_CONTRACTS[value["id"]]["extension"]
            or not isinstance(value.get("searchTerm"), str)
            or not 2 <= len(value["searchTerm"]) <= 64
            or value["searchTerm"] != value["searchTerm"].strip()
            or "\x00" in value["searchTerm"]
            or "\n" in value["searchTerm"]
            or "\r" in value["searchTerm"]
        ):
            raise PaperlessFunctionalLabError("Paperless fixture declaration is invalid")
        if value["id"] == "ocr-zh":
            if not any("\u4e00" <= character <= "\u9fff" for character in value["searchTerm"]):
                raise PaperlessFunctionalLabError("Chinese OCR fixture needs a Chinese search term")
        elif ASCII_TERM.fullmatch(value["searchTerm"]) is None:
            raise PaperlessFunctionalLabError("Paperless non-Chinese search term is invalid")
        by_id[value["id"]] = value
    if set(by_id) != set(FIXTURE_IDS):
        raise PaperlessFunctionalLabError("Paperless fixture set is incomplete")
    expected_names = {FIXTURE_MANIFEST_NAME, *(value["file"] for value in by_id.values())}
    try:
        actual_names = {entry.name for entry in root.iterdir()}
    except OSError as exc:
        raise PaperlessFunctionalLabError(
            "Paperless fixture directory cannot be enumerated"
        ) from exc
    if actual_names != expected_names:
        raise PaperlessFunctionalLabError("Paperless fixture directory has missing or extra files")
    records: list[dict[str, Any]] = []
    total = 0
    for fixture_id in FIXTURE_IDS:
        declaration = by_id[fixture_id]
        raw = _private_file(
            root / declaration["file"],
            f"Paperless fixture {fixture_id}",
            trusted_uid=trusted_uid,
        )
        total += len(raw)
        contract = FIXTURE_CONTRACTS[fixture_id]
        if fixture_id.startswith("ocr-"):
            if not raw.startswith(b"%PDF-"):
                raise PaperlessFunctionalLabError("Paperless OCR fixture is not a PDF")
        else:
            _validate_office(raw, contract["member"], f"Paperless fixture {fixture_id}")
        records.append(
            {
                "id": fixture_id,
                "file": declaration["file"],
                "coverage": contract["coverage"],
                "mediaType": contract["mediaType"],
                "size": len(raw),
                "sha256": _sha256(raw),
                "searchTerm": declaration["searchTerm"],
                "searchTermSha256": _sha256(declaration["searchTerm"].encode("utf-8")),
            }
        )
    if total > MAX_TOTAL_FIXTURE_BYTES:
        raise PaperlessFunctionalLabError("Paperless fixtures exceed the total size limit")
    return records


def _validate_fixture_records(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list) or len(value) != len(FIXTURE_IDS):
        raise PaperlessFunctionalLabError("Paperless fixture evidence set is invalid")
    for index, fixture_id in enumerate(FIXTURE_IDS):
        record = value[index]
        contract = FIXTURE_CONTRACTS[fixture_id]
        if (
            not isinstance(record, dict)
            or set(record)
            != {
                "id",
                "file",
                "coverage",
                "mediaType",
                "size",
                "sha256",
                "searchTermSha256",
            }
            or record["id"] != fixture_id
            or record["coverage"] != contract["coverage"]
            or record["mediaType"] != contract["mediaType"]
            or not isinstance(record["file"], str)
            or SAFE_FILE.fullmatch(record["file"]) is None
            or Path(record["file"]).suffix != contract["extension"]
            or not isinstance(record["size"], int)
            or isinstance(record["size"], bool)
            or not 1 <= record["size"] <= MAX_FIXTURE_BYTES
            or SHA256.fullmatch(str(record["sha256"])) is None
            or SHA256.fullmatch(str(record["searchTermSha256"])) is None
        ):
            raise PaperlessFunctionalLabError("Paperless fixture evidence is invalid")
    if sum(record["size"] for record in value) > MAX_TOTAL_FIXTURE_BYTES:
        raise PaperlessFunctionalLabError("Paperless fixture evidence is oversized")
    return value


def _public_fixture_records(records: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    return [
        {key: value for key, value in record.items() if key != "searchTerm"} for record in records
    ]


def _bundle_identity(
    *, candidate_index: Path, bundle_root: Path, trusted_uid: int
) -> tuple[dict[str, Any], dict[str, Any]]:
    try:
        candidate, bundle = hub_lab._candidate_bundle_identity(
            candidate_index=candidate_index,
            bundle_root=bundle_root,
            trusted_uid=trusted_uid,
        )
        root = Path(bundle["rootPath"])
        raw = hub_lab._read_regular(
            root / "paperless_functional_lab.py",
            "candidate Paperless functional lab tool",
        )
        manifest = hub_lab._strict_json(
            hub_lab._read_regular(root / "bundle-manifest.json", "operations bundle manifest"),
            "operations bundle manifest",
        )
    except hub_lab.HubLifecycleLabError as exc:
        raise PaperlessFunctionalLabError(str(exc)) from exc
    artifact = manifest.get("artifact")
    files = manifest.get("files")
    record = files.get("paperless_functional_lab.py") if isinstance(files, dict) else None
    info = (root / "paperless_functional_lab.py").lstat()
    if (
        not isinstance(artifact, dict)
        or not isinstance(artifact.get("entrypoints"), dict)
        or artifact["entrypoints"].get("paperlessFunctionalLab")
        != "./paperless_functional_lab.py plan|run|verify"
        or record != {"sha256": _sha256(raw), "size": len(raw), "mode": "0755"}
        or info.st_uid != trusted_uid
        or stat.S_IMODE(info.st_mode) != 0o755
    ):
        raise PaperlessFunctionalLabError(
            "Paperless functional lab tool is not from the release candidate"
        )
    return candidate, {
        **bundle,
        "paperlessLabSha256": _sha256(raw),
        "paperlessLabSize": len(raw),
    }


def _paperless_origin(value: str, *, expected_port: int | None = None) -> str:
    parsed = urlsplit(str(value).strip())
    try:
        port = parsed.port
    except ValueError as exc:
        raise PaperlessFunctionalLabError("Paperless URL is invalid") from exc
    if (
        parsed.scheme != "http"
        or parsed.hostname != "127.0.0.1"
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
        or port is None
        or expected_port is not None
        and port != expected_port
    ):
        raise PaperlessFunctionalLabError("Paperless lab URL must be the catalog loopback port")
    return urlunsplit(("http", f"127.0.0.1:{port}", "", "", ""))


def _request(
    method: str,
    url: str,
    headers: Mapping[str, str],
    body: bytes | None,
    maximum: int,
    timeout: float,
) -> tuple[int, Mapping[str, str], bytes]:
    parsed = urlsplit(url)
    origin = _paperless_origin(urlunsplit((parsed.scheme, parsed.netloc, "", "", "")))
    if not parsed.path.startswith("/") or parsed.fragment or origin == "":
        raise PaperlessFunctionalLabError("Paperless API request target is invalid")
    connection = http.client.HTTPConnection(parsed.hostname, parsed.port, timeout=timeout)
    try:
        connection.request(
            method, urlunsplit(("", "", parsed.path, parsed.query, "")), body, dict(headers)
        )
        response = connection.getresponse()
        raw = response.read(maximum + 1)
        response_headers = {key.lower(): value for key, value in response.getheaders()}
    finally:
        connection.close()
    if len(raw) > maximum:
        raise PaperlessFunctionalLabError("Paperless API response is oversized")
    return int(response.status), response_headers, raw


def _json_api(
    request: PaperlessRequest,
    method: str,
    url: str,
    *,
    token: str | None = None,
    payload: Mapping[str, Any] | None = None,
    expected: int = 200,
    timeout: float = 30,
) -> Any:
    body = None
    headers = {"Accept": "application/json", "Connection": "close"}
    if token:
        headers["Authorization"] = f"Token {token}"
    if payload is not None:
        body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode()
        headers["Content-Type"] = "application/json"
    status, _response_headers, raw = request(
        method,
        url,
        headers,
        body,
        MAX_RESPONSE_BYTES,
        timeout,
    )
    if status != expected:
        raise PaperlessFunctionalLabError(f"Paperless API returned HTTP {status}")
    return _strict_json_value(raw, "Paperless API response") if raw else None


def _authenticate(base_url: str, password: str, request: PaperlessRequest) -> str:
    if not password:
        raise PaperlessFunctionalLabError("Paperless administrator password is unavailable")
    value = _json_api(
        request,
        "POST",
        f"{base_url}/api/token/",
        payload={"username": "admin", "password": password},
    )
    token = value.get("token") if isinstance(value, dict) else None
    if not isinstance(token, str) or not 16 <= len(token) <= 512 or "\x00" in token:
        raise PaperlessFunctionalLabError("Paperless authentication returned no bounded token")
    return token


def _multipart(fixture: Mapping[str, Any], raw: bytes, plan_id: str) -> tuple[str, bytes]:
    for nonce in range(16):
        boundary_seed = f"{plan_id}:{fixture['id']}:{nonce}".encode()
        boundary = f"echo-{_sha256(boundary_seed)[:48]}"
        if boundary.encode() not in raw:
            break
    else:  # pragma: no cover - cryptographically unreachable without a malicious fixture
        raise PaperlessFunctionalLabError("Paperless fixture collides with multipart boundary")
    title = f"Echo physical acceptance {fixture['id']} {plan_id[:12]}"
    body = b"".join(
        (
            f"--{boundary}\r\n".encode(),
            b'Content-Disposition: form-data; name="title"\r\n\r\n',
            title.encode(),
            b"\r\n",
            f"--{boundary}\r\n".encode(),
            (
                f'Content-Disposition: form-data; name="document"; filename="{fixture["file"]}"\r\n'
            ).encode(),
            f"Content-Type: {fixture['mediaType']}\r\n\r\n".encode(),
            raw,
            b"\r\n",
            f"--{boundary}--\r\n".encode(),
        )
    )
    return boundary, body


def _upload(
    *,
    base_url: str,
    token: str,
    fixture: Mapping[str, Any],
    raw: bytes,
    plan_id: str,
    request: PaperlessRequest,
) -> str:
    boundary, body = _multipart(fixture, raw, plan_id)
    status, _headers, response = request(
        "POST",
        f"{base_url}/api/documents/post_document/",
        {
            "Accept": "application/json",
            "Authorization": f"Token {token}",
            "Connection": "close",
            "Content-Type": f"multipart/form-data; boundary={boundary}",
        },
        body,
        MAX_RESPONSE_BYTES,
        120,
    )
    value = _strict_json_value(response, "Paperless upload response")
    task_id = value if isinstance(value, str) else None
    try:
        canonical = str(uuid.UUID(task_id or ""))
    except ValueError as exc:
        raise PaperlessFunctionalLabError("Paperless upload returned no task UUID") from exc
    if status != 200 or canonical != task_id:
        raise PaperlessFunctionalLabError("Paperless upload response is invalid")
    return task_id


def _wait_task(
    *,
    base_url: str,
    token: str,
    task_id: str,
    request: PaperlessRequest,
    clock: Callable[[], float],
    sleeper: Callable[[float], None],
) -> int:
    deadline = clock() + TASK_TIMEOUT_SECONDS
    while clock() <= deadline:
        value = _json_api(
            request,
            "GET",
            f"{base_url}/api/tasks/?task_id={quote(task_id, safe='')}",
            token=token,
        )
        records = value.get("results") if isinstance(value, dict) else value
        if isinstance(records, list) and len(records) == 1 and isinstance(records[0], dict):
            record = records[0]
            status = str(record.get("status") or "").lower()
            if status == "success":
                ids = record.get("related_document_ids")
                document_id = ids[0] if isinstance(ids, list) and len(ids) == 1 else None
                if document_id is None:
                    document_id = record.get("related_document")
                if (
                    not isinstance(document_id, int)
                    or isinstance(document_id, bool)
                    or document_id <= 0
                ):
                    raise PaperlessFunctionalLabError(
                        "Paperless task succeeded without one document identity"
                    )
                return document_id
            if status in {"failure", "revoked"}:
                raise PaperlessFunctionalLabError("Paperless consumption task failed")
        sleeper(POLL_SECONDS)
    raise PaperlessFunctionalLabError("Paperless consumption task timed out")


def _wait_search(
    *,
    base_url: str,
    token: str,
    term: str,
    document_id: int,
    request: PaperlessRequest,
    clock: Callable[[], float],
    sleeper: Callable[[float], None],
) -> None:
    deadline = clock() + SEARCH_TIMEOUT_SECONDS
    query = quote(term, safe="")
    while clock() <= deadline:
        value = _json_api(
            request,
            "GET",
            f"{base_url}/api/documents/?text={query}&page_size=100",
            token=token,
        )
        records = value.get("results") if isinstance(value, dict) else None
        if isinstance(records, list) and any(
            isinstance(record, dict) and record.get("id") == document_id for record in records
        ):
            return
        sleeper(POLL_SECONDS)
    raise PaperlessFunctionalLabError("Paperless extracted text did not become searchable")


def _download_original(
    *,
    base_url: str,
    token: str,
    document_id: int,
    expected_size: int,
    request: PaperlessRequest,
) -> tuple[int, str]:
    status, _headers, raw = request(
        "GET",
        f"{base_url}/api/documents/{document_id}/download/?original=true",
        {"Authorization": f"Token {token}", "Connection": "close"},
        None,
        expected_size,
        120,
    )
    if status != 200 or len(raw) != expected_size:
        raise PaperlessFunctionalLabError("Paperless original export size is invalid")
    return len(raw), _sha256(raw)


def _delete_document(
    *, base_url: str, token: str, document_id: int, request: PaperlessRequest
) -> int:
    status, _headers, raw = request(
        "DELETE",
        f"{base_url}/api/documents/{document_id}/",
        {"Authorization": f"Token {token}", "Connection": "close"},
        None,
        1024,
        30,
    )
    if status != 204 or raw:
        raise PaperlessFunctionalLabError("Paperless fixture cleanup failed")
    return status


def build_plan(
    *,
    base_url: str,
    catalog: Mapping[str, Any],
    candidate_index: Path,
    bundle_root: Path,
    fixture_directory: Path,
    output: Path,
    trusted_uid: int | None = None,
    docker: hub_lab.DockerJson = hub_lab._docker_json,
) -> dict[str, Any]:
    uid = os.getuid() if trusted_uid is None else trusted_uid
    snapshot = hub_lab._catalog_snapshot(catalog, expected_installed=(APP_ID,))
    contract = snapshot["apps"][APP_ID]
    endpoint = contract["endpoint"]
    origin = _paperless_origin(base_url, expected_port=endpoint["port"])
    candidate, bundle = _bundle_identity(
        candidate_index=candidate_index,
        bundle_root=bundle_root,
        trusted_uid=uid,
    )
    runtime = hub_lab._running_candidate(candidate["immutableReference"], docker)
    installation = hub_lab.inspect_installation(APP_ID, contract, docker)
    fixtures = _public_fixture_records(_fixture_snapshot(fixture_directory, trusted_uid=uid))
    identity: dict[str, Any] = {
        "schemaVersion": SCHEMA_VERSION,
        "kind": PLAN_KIND,
        "baseUrl": origin,
        "releaseCandidate": candidate,
        "operationsBundle": bundle,
        "runtime": runtime,
        "catalog": snapshot,
        "installation": installation,
        "fixtures": fixtures,
        "workflow": ["authenticate", "upload", "task", "search", "export-original", "cleanup"],
    }
    plan_id = _sha256(_canonical(identity))
    value = {
        **identity,
        "planId": plan_id,
        "confirmation": f"RUN ECHO PAPERLESS FUNCTIONAL LAB {plan_id}",
    }
    try:
        hub_lab._write_new(output, value, mode=0o400)
    except hub_lab.HubLifecycleLabError as exc:
        raise PaperlessFunctionalLabError(str(exc)) from exc
    return value


def _validate_plan_value(value: Any) -> dict[str, Any]:
    required = {
        "schemaVersion",
        "kind",
        "baseUrl",
        "releaseCandidate",
        "operationsBundle",
        "runtime",
        "catalog",
        "installation",
        "fixtures",
        "workflow",
        "planId",
        "confirmation",
    }
    if (
        not isinstance(value, dict)
        or set(value) != required
        or value.get("schemaVersion") != SCHEMA_VERSION
        or value.get("kind") != PLAN_KIND
    ):
        raise PaperlessFunctionalLabError("Paperless functional plan contract is invalid")
    identity = {key: item for key, item in value.items() if key not in {"planId", "confirmation"}}
    plan_id = _sha256(_canonical(identity))
    try:
        catalog = hub_lab._validate_catalog_snapshot_value(value["catalog"])
        hub_lab._validate_installation_evidence(
            APP_ID,
            catalog["apps"][APP_ID],
            value["installation"],
        )
    except hub_lab.HubLifecycleLabError as exc:
        raise PaperlessFunctionalLabError(str(exc)) from exc
    candidate = value["releaseCandidate"]
    bundle = value["operationsBundle"]
    runtime = value["runtime"]
    if (
        value["planId"] != plan_id
        or value["confirmation"] != f"RUN ECHO PAPERLESS FUNCTIONAL LAB {plan_id}"
        or _paperless_origin(
            value["baseUrl"],
            expected_port=catalog["apps"][APP_ID]["endpoint"]["port"],
        )
        != value["baseUrl"]
        or value["workflow"]
        != ["authenticate", "upload", "task", "search", "export-original", "cleanup"]
        or not isinstance(candidate, dict)
        or not isinstance(bundle, dict)
        or bundle.get("artifactId") != candidate.get("operationsArtifactId")
        or bundle.get("archiveSha256") != candidate.get("operationsArchiveSha256")
        or bundle.get("imageReference") != candidate.get("immutableReference")
        or SHA256.fullmatch(str(bundle.get("paperlessLabSha256") or "")) is None
        or not isinstance(bundle.get("paperlessLabSize"), int)
        or isinstance(bundle.get("paperlessLabSize"), bool)
        or bundle["paperlessLabSize"] <= 0
        or not isinstance(runtime, dict)
        or set(runtime) != {"main", "proxy", "discovery"}
        or any(
            not isinstance(runtime.get(role), dict)
            or runtime[role].get("image") != candidate.get("immutableReference")
            or SHA256.fullmatch(str(runtime[role].get("containerId") or "")) is None
            for role in runtime
        )
    ):
        raise PaperlessFunctionalLabError("Paperless functional plan identity is invalid")
    _validate_fixture_records(value["fixtures"])
    return value


def load_plan(path: Path) -> dict[str, Any]:
    try:
        if os.name != "nt" and stat.S_IMODE(path.stat().st_mode) != 0o400:
            raise PaperlessFunctionalLabError("Paperless functional plan must be mode 0400")
        raw = hub_lab._read_regular(path, "Paperless functional plan")
    except (OSError, hub_lab.HubLifecycleLabError) as exc:
        raise PaperlessFunctionalLabError(str(exc)) from exc
    return _validate_plan_value(_strict_json_value(raw, "Paperless functional plan"))


def _private_password(path: Path, plan: Mapping[str, Any]) -> str:
    try:
        info = path.lstat()
        parent = path.parent.resolve(strict=True)
        parent_info = parent.stat()
        if (
            path.name != hub_lab.PAPERLESS_PRIVATE_SECRET_NAME
            or path.is_symlink()
            or not stat.S_ISREG(info.st_mode)
            or info.st_nlink != 1
            or info.st_uid != os.getuid()
            or stat.S_IMODE(info.st_mode) != 0o400
            or not parent.is_dir()
            or parent_info.st_uid != os.getuid()
            or stat.S_IMODE(parent_info.st_mode) != 0o700
        ):
            raise PaperlessFunctionalLabError(
                "Paperless private password file must be owner-only mode 0400"
            )
        raw = hub_lab._read_regular(path, "Paperless private password file")
    except (OSError, hub_lab.HubLifecycleLabError) as exc:
        raise PaperlessFunctionalLabError(str(exc)) from exc
    value = _strict_json_value(raw, "Paperless private password file")
    required = {
        "schemaVersion",
        "kind",
        "appId",
        "secretName",
        "hubLifecyclePlanId",
        "releaseCandidate",
        "password",
    }
    if (
        not isinstance(value, dict)
        or set(value) != required
        or value.get("schemaVersion") != 1
        or value.get("kind") != hub_lab.PAPERLESS_PRIVATE_SECRET_KIND
        or value.get("appId") != APP_ID
        or value.get("secretName") != "admin-password"
        or SHA256.fullmatch(str(value.get("hubLifecyclePlanId") or "")) is None
        or value.get("releaseCandidate") != plan["releaseCandidate"]
        or re.fullmatch(r"[A-Za-z0-9]{24}", str(value.get("password") or "")) is None
    ):
        raise PaperlessFunctionalLabError(
            "Paperless private password file is not bound to this candidate"
        )
    return value["password"]


def run_plan(
    *,
    plan_path: Path,
    fixture_directory: Path,
    confirmation: str,
    password: str = "",
    private_secret_path: Path | None = None,
    output: Path,
    request: PaperlessRequest = _request,
    docker: hub_lab.DockerJson = hub_lab._docker_json,
    clock: Callable[[], float] = time.monotonic,
    sleeper: Callable[[float], None] = time.sleep,
) -> dict[str, Any]:
    plan = load_plan(plan_path)
    if private_secret_path is not None:
        if password:
            raise PaperlessFunctionalLabError(
                "Paperless password must use either one private file or one direct secret"
            )
        private_parent = private_secret_path.parent.resolve(strict=True)
        public_parent = plan_path.parent.resolve(strict=True)
        if hub_lab._is_within(private_parent, public_parent):
            raise PaperlessFunctionalLabError(
                "Paperless private password must stay outside public evidence"
            )
        password = _private_password(private_secret_path, plan)
    elif not password:
        raise PaperlessFunctionalLabError("Paperless administrator password is unavailable")
    if confirmation != plan["confirmation"]:
        raise PaperlessFunctionalLabError("Paperless functional confirmation is invalid")
    candidate, bundle = _bundle_identity(
        candidate_index=Path(plan["releaseCandidate"]["indexPath"]),
        bundle_root=Path(plan["operationsBundle"]["rootPath"]),
        trusted_uid=os.getuid(),
    )
    contract = plan["catalog"]["apps"][APP_ID]
    if (
        candidate != plan["releaseCandidate"]
        or bundle != plan["operationsBundle"]
        or hub_lab._running_candidate(candidate["immutableReference"], docker) != plan["runtime"]
        or hub_lab.inspect_installation(APP_ID, contract, docker) != plan["installation"]
    ):
        raise PaperlessFunctionalLabError("Paperless functional inputs changed after review")
    private_fixtures = _fixture_snapshot(fixture_directory, trusted_uid=os.getuid())
    if _public_fixture_records(private_fixtures) != plan["fixtures"]:
        raise PaperlessFunctionalLabError("Paperless functional fixtures changed after review")
    token = _authenticate(plan["baseUrl"], password, request)
    records: dict[str, Any] = {}
    root = fixture_directory.resolve(strict=True)
    for index, fixture in enumerate(plan["fixtures"]):
        private_fixture = private_fixtures[index]
        raw = _private_file(
            root / fixture["file"],
            f"Paperless fixture {fixture['id']}",
            trusted_uid=os.getuid(),
        )
        task_id = _upload(
            base_url=plan["baseUrl"],
            token=token,
            fixture=fixture,
            raw=raw,
            plan_id=plan["planId"],
            request=request,
        )
        document_id = _wait_task(
            base_url=plan["baseUrl"],
            token=token,
            task_id=task_id,
            request=request,
            clock=clock,
            sleeper=sleeper,
        )
        _wait_search(
            base_url=plan["baseUrl"],
            token=token,
            term=private_fixture["searchTerm"],
            document_id=document_id,
            request=request,
            clock=clock,
            sleeper=sleeper,
        )
        downloaded_size, downloaded_sha = _download_original(
            base_url=plan["baseUrl"],
            token=token,
            document_id=document_id,
            expected_size=fixture["size"],
            request=request,
        )
        if downloaded_sha != fixture["sha256"]:
            raise PaperlessFunctionalLabError("Paperless original export digest changed")
        cleanup_status = _delete_document(
            base_url=plan["baseUrl"],
            token=token,
            document_id=document_id,
            request=request,
        )
        records[fixture["id"]] = {
            "sourceSha256": fixture["sha256"],
            "sourceBytes": fixture["size"],
            "taskIdSha256": _sha256(task_id.encode()),
            "taskStatus": "success",
            "documentIdSha256": _sha256(f"{plan['planId']}:{document_id}".encode()),
            "searchTermSha256": fixture["searchTermSha256"],
            "searchMatched": True,
            "originalDownloadSha256": downloaded_sha,
            "originalDownloadBytes": downloaded_size,
            "cleanupStatus": cleanup_status,
        }
    value: dict[str, Any] = {
        "schemaVersion": SCHEMA_VERSION,
        "kind": RESULT_KIND,
        "planId": plan["planId"],
        "releaseCandidate": plan["releaseCandidate"],
        "operationsBundle": plan["operationsBundle"],
        "catalogDigest": plan["catalog"]["digest"],
        "architecture": plan["catalog"]["architecture"],
        "fixtures": records,
        "checks": {
            "chineseOcrVerified": True,
            "englishOcrVerified": True,
            "docxConvertedAndSearched": True,
            "xlsxConvertedAndSearched": True,
            "pptxConvertedAndSearched": True,
            "originalExportsVerified": True,
            "fixturesCleaned": True,
        },
        "allPassed": True,
        "completedAtUnix": int(time.time()),
    }
    value["resultId"] = _sha256(_canonical(value))
    try:
        hub_lab._write_new(output, value, mode=0o444)
    except hub_lab.HubLifecycleLabError as exc:
        raise PaperlessFunctionalLabError(str(exc)) from exc
    return value


def _validate_result_value(
    plan: Mapping[str, Any], value: Any, *, now: int | None = None
) -> dict[str, Any]:
    required = {
        "schemaVersion",
        "kind",
        "planId",
        "releaseCandidate",
        "operationsBundle",
        "catalogDigest",
        "architecture",
        "fixtures",
        "checks",
        "allPassed",
        "completedAtUnix",
        "resultId",
    }
    if not isinstance(value, dict):
        raise PaperlessFunctionalLabError("Paperless functional result is invalid")
    unsigned = dict(value)
    result_id = unsigned.pop("resultId", None)
    expected_checks = {
        "chineseOcrVerified",
        "englishOcrVerified",
        "docxConvertedAndSearched",
        "xlsxConvertedAndSearched",
        "pptxConvertedAndSearched",
        "originalExportsVerified",
        "fixturesCleaned",
    }
    if (
        set(value) != required
        or value.get("schemaVersion") != SCHEMA_VERSION
        or value.get("kind") != RESULT_KIND
        or value.get("planId") != plan["planId"]
        or value.get("releaseCandidate") != plan["releaseCandidate"]
        or value.get("operationsBundle") != plan["operationsBundle"]
        or value.get("catalogDigest") != plan["catalog"]["digest"]
        or value.get("architecture") != plan["catalog"]["architecture"]
        or not isinstance(value.get("checks"), dict)
        or set(value["checks"]) != expected_checks
        or any(value["checks"].get(check) is not True for check in expected_checks)
        or value.get("allPassed") is not True
        or not isinstance(value.get("completedAtUnix"), int)
        or isinstance(value.get("completedAtUnix"), bool)
        or not 0 < value["completedAtUnix"] <= (int(time.time()) if now is None else now) + 300
        or result_id != _sha256(_canonical(unsigned))
    ):
        raise PaperlessFunctionalLabError("Paperless functional result identity is invalid")
    records = value.get("fixtures")
    if not isinstance(records, dict) or set(records) != set(FIXTURE_IDS):
        raise PaperlessFunctionalLabError("Paperless functional result fixture set is invalid")
    planned = {fixture["id"]: fixture for fixture in plan["fixtures"]}
    for fixture_id, record in records.items():
        fixture = planned[fixture_id]
        if (
            not isinstance(record, dict)
            or set(record)
            != {
                "sourceSha256",
                "sourceBytes",
                "taskIdSha256",
                "taskStatus",
                "documentIdSha256",
                "searchTermSha256",
                "searchMatched",
                "originalDownloadSha256",
                "originalDownloadBytes",
                "cleanupStatus",
            }
            or record["sourceSha256"] != fixture["sha256"]
            or record["sourceBytes"] != fixture["size"]
            or SHA256.fullmatch(str(record["taskIdSha256"])) is None
            or record["taskStatus"] != "success"
            or SHA256.fullmatch(str(record["documentIdSha256"])) is None
            or record["searchTermSha256"] != fixture["searchTermSha256"]
            or record["searchMatched"] is not True
            or record["originalDownloadSha256"] != fixture["sha256"]
            or record["originalDownloadBytes"] != fixture["size"]
            or record["cleanupStatus"] != 204
        ):
            raise PaperlessFunctionalLabError("Paperless functional fixture result is invalid")
    return value


def validate_evidence_bytes(
    plan_raw: bytes,
    result_raw: bytes,
    *,
    expected_candidate: Mapping[str, str] | None = None,
    now: int | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    plan = _validate_plan_value(_strict_json_value(plan_raw, "Paperless functional plan"))
    result = _validate_result_value(
        plan,
        _strict_json_value(result_raw, "Paperless functional result"),
        now=now,
    )
    if expected_candidate is not None:
        shared = (
            "indexId",
            "sourceRevision",
            "agentRevision",
            "releaseTag",
            "operationsArtifactId",
            "operationsArchiveSha256",
            "immutableReference",
        )
        if any(
            field not in expected_candidate
            or plan["releaseCandidate"].get(field) != expected_candidate[field]
            for field in shared
        ):
            raise PaperlessFunctionalLabError(
                "Paperless functional evidence belongs to another release candidate"
            )
    return plan, result


def verify_result(
    *,
    plan_path: Path,
    result_path: Path,
    docker: hub_lab.DockerJson = hub_lab._docker_json,
) -> dict[str, Any]:
    plan = load_plan(plan_path)
    try:
        if os.name != "nt" and stat.S_IMODE(result_path.stat().st_mode) != 0o444:
            raise PaperlessFunctionalLabError("Paperless functional result must be mode 0444")
        raw = hub_lab._read_regular(result_path, "Paperless functional result")
    except (OSError, hub_lab.HubLifecycleLabError) as exc:
        raise PaperlessFunctionalLabError(str(exc)) from exc
    value = _validate_result_value(
        plan,
        _strict_json_value(raw, "Paperless functional result"),
    )
    candidate, bundle = _bundle_identity(
        candidate_index=Path(plan["releaseCandidate"]["indexPath"]),
        bundle_root=Path(plan["operationsBundle"]["rootPath"]),
        trusted_uid=os.getuid(),
    )
    contract = plan["catalog"]["apps"][APP_ID]
    if (
        candidate != plan["releaseCandidate"]
        or bundle != plan["operationsBundle"]
        or hub_lab._running_candidate(candidate["immutableReference"], docker) != plan["runtime"]
        or hub_lab.inspect_installation(APP_ID, contract, docker) != plan["installation"]
    ):
        raise PaperlessFunctionalLabError("Paperless verification runtime changed")
    return value


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    plan = subparsers.add_parser("plan")
    plan.add_argument("--echo-base-url", default="http://127.0.0.1:8000")
    plan.add_argument("--echo-password-env", default="ECHO_ADMIN_PASSWORD")
    plan.add_argument("--paperless-base-url", default="http://127.0.0.1:3008")
    plan.add_argument("--candidate-index", required=True, type=Path)
    plan.add_argument("--bundle-root", required=True, type=Path)
    plan.add_argument("--fixture-directory", required=True, type=Path)
    plan.add_argument("--output", required=True, type=Path)
    run = subparsers.add_parser("run")
    run.add_argument("--plan", required=True, type=Path)
    run.add_argument("--fixture-directory", required=True, type=Path)
    run.add_argument("--confirmation", required=True)
    run.add_argument("--password-env", default="PAPERLESS_ADMIN_PASSWORD")
    run.add_argument("--password-file", type=Path)
    run.add_argument("--output", required=True, type=Path)
    verify = subparsers.add_parser("verify")
    verify.add_argument("--plan", required=True, type=Path)
    verify.add_argument("--result", required=True, type=Path)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if sys.platform != "linux" or os.geteuid() != 0:
            raise PaperlessFunctionalLabError(
                "Paperless functional lab requires Linux root on the appliance host"
            )
        if args.command == "plan":
            echo_password = os.environ.get(args.echo_password_env, "")
            if not echo_password:
                raise PaperlessFunctionalLabError("Echo administrator password is unavailable")
            echo_base_url = hub_lab._origin(args.echo_base_url)
            echo_token = hub_lab._login(echo_base_url, echo_password, hub_lab._http_request)
            catalog = hub_lab._request(
                hub_lab._http_request,
                "GET",
                f"{echo_base_url}/api/appliance/hub/catalog",
                token=echo_token,
            )
            result = build_plan(
                base_url=args.paperless_base_url,
                catalog=catalog,
                candidate_index=args.candidate_index,
                bundle_root=args.bundle_root,
                fixture_directory=args.fixture_directory,
                output=args.output,
            )
        elif args.command == "run":
            password = (
                "" if args.password_file is not None else os.environ.get(args.password_env, "")
            )
            result = run_plan(
                plan_path=args.plan,
                fixture_directory=args.fixture_directory,
                confirmation=args.confirmation,
                password=password,
                private_secret_path=args.password_file,
                output=args.output,
            )
        else:
            result = verify_result(plan_path=args.plan, result_path=args.result)
    except (
        OSError,
        KeyError,
        TypeError,
        ValueError,
        zipfile.BadZipFile,
        hub_lab.HubLifecycleLabError,
        PaperlessFunctionalLabError,
    ) as exc:
        print(f"PAPERLESS_FUNCTIONAL_LAB_ERROR: {exc}", file=sys.stderr)
        return 1
    print(
        "PAPERLESS_FUNCTIONAL_LAB_OK "
        f"kind={result['kind']} plan={result['planId']} "
        f"fixtures={len(result.get('fixtures') or [])}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
