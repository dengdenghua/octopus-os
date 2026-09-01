from __future__ import annotations

import copy
import hashlib
import io
import json
import stat
import uuid
import zipfile
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit

import pytest

from deploy.appliance import hub_lifecycle_lab as hub_lab
from deploy.appliance import paperless_functional_lab as lab
from tests.appliance.test_hub_lifecycle_lab import (
    _catalog,
    _LifecycleDocker,
    _release,
)


def _ooxml(member: str, text: str) -> bytes:
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(
            "[Content_Types].xml",
            '<?xml version="1.0"?><Types xmlns="http://schemas.openxmlformats.org/'
            'package/2006/content-types"><Default Extension="xml" '
            'ContentType="application/xml"/></Types>',
        )
        archive.writestr(member, f'<?xml version="1.0"?><fixture>{text}</fixture>')
    return output.getvalue()


def _fixtures(root: Path) -> tuple[Path, dict[str, bytes], dict[str, str]]:
    root.mkdir(mode=0o700)
    files = {
        "ocr-zh.pdf": b"%PDF-1.4\nimage-only chinese fixture\n%%EOF\n",
        "ocr-en.pdf": b"%PDF-1.4\nimage-only english fixture\n%%EOF\n",
        "office.docx": _ooxml("word/document.xml", "ECHO DOCX ACCEPTANCE 1931"),
        "office.xlsx": _ooxml("xl/workbook.xml", "ECHO XLSX ACCEPTANCE 1932"),
        "office.pptx": _ooxml("ppt/presentation.xml", "ECHO PPTX ACCEPTANCE 1933"),
    }
    terms = {
        "ocr-zh": "章鱼系统中文识别验收",
        "ocr-en": "ECHO OCR ACCEPTANCE 1930",
        "office-docx": "ECHO DOCX ACCEPTANCE 1931",
        "office-xlsx": "ECHO XLSX ACCEPTANCE 1932",
        "office-pptx": "ECHO PPTX ACCEPTANCE 1933",
    }
    names = {
        "ocr-zh": "ocr-zh.pdf",
        "ocr-en": "ocr-en.pdf",
        "office-docx": "office.docx",
        "office-xlsx": "office.xlsx",
        "office-pptx": "office.pptx",
    }
    manifest = {
        "schemaVersion": 1,
        "kind": lab.FIXTURE_KIND,
        "fixtures": [
            {"id": fixture_id, "file": names[fixture_id], "searchTerm": terms[fixture_id]}
            for fixture_id in lab.FIXTURE_IDS
        ],
    }
    manifest_path = root / lab.FIXTURE_MANIFEST_NAME
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False) + "\n", encoding="utf-8")
    manifest_path.chmod(0o400)
    for name, raw in files.items():
        path = root / name
        path.write_bytes(raw)
        path.chmod(0o400)
    return root, files, terms


def _installed_catalog() -> dict[str, Any]:
    catalog = copy.deepcopy(_catalog())
    app = next(item for item in catalog["apps"] if item["id"] == lab.APP_ID)
    app["installation"]["installed"] = True
    app["installable"] = False
    app["installBlockers"] = ["PORT_IN_USE", "ALREADY_INSTALLED"]
    return catalog


def _docker(catalog: dict[str, Any]) -> _LifecycleDocker:
    snapshot = hub_lab._catalog_snapshot(catalog, expected_installed=(lab.APP_ID,))
    docker = _LifecycleDocker(snapshot["apps"])
    docker.installed.add(lab.APP_ID)
    return docker


class _PaperlessApi:
    def __init__(
        self,
        files: dict[str, bytes],
        terms: dict[str, str],
        *,
        password: str = "paperless-secret",
    ) -> None:
        self.files = files
        self.terms = terms
        self.password = password
        self.task_to_name: dict[str, str] = {}
        self.document_to_name: dict[int, str] = {}
        self.deleted: list[int] = []

    def __call__(
        self,
        method: str,
        url: str,
        headers: dict[str, str],
        body: bytes | None,
        maximum: int,
        timeout: float,
    ) -> tuple[int, dict[str, str], bytes]:
        del maximum, timeout
        parsed = urlsplit(url)
        path = parsed.path
        if path == "/api/token/":
            assert method == "POST"
            assert json.loads(body or b"") == {
                "username": "admin",
                "password": self.password,
            }
            return 200, {"content-type": "application/json"}, b'{"token":"t' + b"1" * 32 + b'"}'
        assert headers["Authorization"].startswith("Token ")
        if path == "/api/documents/post_document/":
            assert method == "POST" and body is not None
            matches = [name for name, raw in self.files.items() if raw in body]
            assert len(matches) == 1
            task_id = str(uuid.uuid5(uuid.NAMESPACE_URL, matches[0]))
            self.task_to_name[task_id] = matches[0]
            return 200, {}, json.dumps(task_id).encode()
        if path == "/api/tasks/":
            task_id = parse_qs(parsed.query)["task_id"][0]
            name = self.task_to_name[task_id]
            document_id = list(self.files).index(name) + 100
            self.document_to_name[document_id] = name
            return (
                200,
                {},
                json.dumps(
                    {
                        "count": 1,
                        "results": [
                            {
                                "task_id": task_id,
                                "status": "success",
                                "related_document_ids": [document_id],
                            }
                        ],
                    }
                ).encode(),
            )
        if path == "/api/documents/" and method == "GET":
            term = parse_qs(parsed.query)["text"][0]
            fixture_id = next(key for key, value in self.terms.items() if value == term)
            names = {
                "ocr-zh": "ocr-zh.pdf",
                "ocr-en": "ocr-en.pdf",
                "office-docx": "office.docx",
                "office-xlsx": "office.xlsx",
                "office-pptx": "office.pptx",
            }
            document_id = list(self.files).index(names[fixture_id]) + 100
            return 200, {}, json.dumps({"count": 1, "results": [{"id": document_id}]}).encode()
        download = path.removeprefix("/api/documents/").removesuffix("/download/")
        if download.isdigit() and method == "GET":
            name = self.document_to_name[int(download)]
            return 200, {"content-type": "application/octet-stream"}, self.files[name]
        delete = path.removeprefix("/api/documents/").removesuffix("/")
        if delete.isdigit() and method == "DELETE":
            self.deleted.append(int(delete))
            return 204, {}, b""
        raise AssertionError((method, url))


def test_plan_binds_candidate_catalog_installation_and_private_fixture_hashes(
    tmp_path: Path,
) -> None:
    candidate, bundle_root = _release(tmp_path)
    fixture_root, files, _terms = _fixtures(tmp_path / "fixtures")
    catalog = _installed_catalog()
    docker = _docker(catalog)
    output = tmp_path / "paperless-plan.json"

    plan = lab.build_plan(
        base_url="http://127.0.0.1:3008/",
        catalog=catalog,
        candidate_index=candidate,
        bundle_root=bundle_root,
        fixture_directory=fixture_root,
        output=output,
        docker=docker,
    )

    assert plan["kind"] == lab.PLAN_KIND
    assert plan["catalog"]["apps"][lab.APP_ID]["endpoint"]["port"] == 3008
    assert set(plan["installation"]["services"]) == {
        "cache",
        "database",
        "gotenberg",
        "tika",
        "app",
    }
    assert [record["id"] for record in plan["fixtures"]] == list(lab.FIXTURE_IDS)
    assert {record["file"]: record["sha256"] for record in plan["fixtures"]} == {
        name: hashlib.sha256(raw).hexdigest() for name, raw in files.items()
    }
    assert str(fixture_root) not in output.read_text(encoding="utf-8")
    assert "章鱼系统中文识别验收" not in output.read_text(encoding="utf-8")
    assert "ECHO DOCX ACCEPTANCE" not in output.read_text(encoding="utf-8")
    assert stat.S_IMODE(output.stat().st_mode) == 0o400
    assert lab.load_plan(output) == plan


def test_run_uploads_searches_exports_and_cleans_all_five_real_formats(
    tmp_path: Path,
) -> None:
    candidate, bundle_root = _release(tmp_path)
    fixture_root, files, terms = _fixtures(tmp_path / "fixtures")
    catalog = _installed_catalog()
    docker = _docker(catalog)
    evidence_root = tmp_path / "evidence"
    evidence_root.mkdir(mode=0o700)
    private_root = tmp_path / "private"
    private_root.mkdir(mode=0o700)
    plan_path = evidence_root / "paperless-plan.json"
    plan = lab.build_plan(
        base_url="http://127.0.0.1:3008",
        catalog=catalog,
        candidate_index=candidate,
        bundle_root=bundle_root,
        fixture_directory=fixture_root,
        output=plan_path,
        docker=docker,
    )
    result_path = evidence_root / "paperless-result.json"
    password = "PaperlessSecret202608290"
    secret_path = private_root / hub_lab.PAPERLESS_PRIVATE_SECRET_NAME
    secret_path.write_bytes(
        hub_lab._canonical(
            {
                "schemaVersion": 1,
                "kind": hub_lab.PAPERLESS_PRIVATE_SECRET_KIND,
                "appId": lab.APP_ID,
                "secretName": "admin-password",
                "hubLifecyclePlanId": "f" * 64,
                "releaseCandidate": plan["releaseCandidate"],
                "password": password,
            }
        )
    )
    secret_path.chmod(0o400)
    api = _PaperlessApi(files, terms, password=password)

    result = lab.run_plan(
        plan_path=plan_path,
        fixture_directory=fixture_root,
        confirmation=plan["confirmation"],
        private_secret_path=secret_path,
        output=result_path,
        request=api,
        docker=docker,
        clock=lambda: 100.0,
        sleeper=lambda _seconds: None,
    )

    assert result["allPassed"] is True
    assert set(result["fixtures"]) == set(lab.FIXTURE_IDS)
    assert all(record["searchMatched"] is True for record in result["fixtures"].values())
    assert all(
        record["sourceSha256"] == record["originalDownloadSha256"]
        for record in result["fixtures"].values()
    )
    assert len(api.deleted) == len(lab.FIXTURE_IDS)
    evidence = result_path.read_text(encoding="utf-8")
    assert password not in evidence
    assert "Token " not in evidence
    assert "章鱼系统中文识别验收" not in evidence
    assert stat.S_IMODE(result_path.stat().st_mode) == 0o444
    assert lab.verify_result(plan_path=plan_path, result_path=result_path, docker=docker) == result
    plan_raw = plan_path.read_bytes()
    result_raw = result_path.read_bytes()
    assert lab.validate_evidence_bytes(
        plan_raw,
        result_raw,
        expected_candidate=plan["releaseCandidate"],
    ) == (plan, result)

    forged = copy.deepcopy(result)
    forged["fixtures"]["ocr-zh"]["searchMatched"] = False
    unsigned = dict(forged)
    unsigned.pop("resultId")
    forged["resultId"] = hashlib.sha256(lab._canonical(unsigned)).hexdigest()
    with pytest.raises(lab.PaperlessFunctionalLabError, match="fixture result"):
        lab.validate_evidence_bytes(plan_raw, lab._canonical(forged))

    secret_path.chmod(0o600)
    with pytest.raises(lab.PaperlessFunctionalLabError, match="mode 0400"):
        lab._private_password(secret_path, plan)
    secret_path.chmod(0o400)
    linked_root = tmp_path / "linked-private"
    linked_root.mkdir(mode=0o700)
    linked_secret = linked_root / hub_lab.PAPERLESS_PRIVATE_SECRET_NAME
    linked_secret.hardlink_to(secret_path)
    with pytest.raises(lab.PaperlessFunctionalLabError, match="mode 0400"):
        lab._private_password(linked_secret, plan)
    linked_secret.unlink()
    secret_path.chmod(0o600)
    secret = json.loads(secret_path.read_text(encoding="utf-8"))
    secret["releaseCandidate"]["sourceRevision"] = "0" * 40
    secret_path.write_bytes(hub_lab._canonical(secret))
    secret_path.chmod(0o400)
    with pytest.raises(lab.PaperlessFunctionalLabError, match="not bound"):
        lab._private_password(secret_path, plan)


def test_fixture_boundary_rejects_extra_files_wrong_permissions_and_non_chinese_term(
    tmp_path: Path,
) -> None:
    root, _files, _terms = _fixtures(tmp_path / "fixtures")
    extra = root / "extra.txt"
    extra.write_text("unexpected", encoding="utf-8")
    extra.chmod(0o400)
    with pytest.raises(lab.PaperlessFunctionalLabError, match="missing or extra"):
        lab._fixture_snapshot(root, trusted_uid=root.stat().st_uid)
    extra.unlink()

    document = root / "office.docx"
    document.chmod(0o600)
    with pytest.raises(lab.PaperlessFunctionalLabError, match="mode-0400"):
        lab._fixture_snapshot(root, trusted_uid=root.stat().st_uid)
    document.chmod(0o400)

    manifest_path = root / lab.FIXTURE_MANIFEST_NAME
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["fixtures"][0]["searchTerm"] = "not chinese"
    manifest_path.chmod(0o600)
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    manifest_path.chmod(0o400)
    with pytest.raises(lab.PaperlessFunctionalLabError, match="Chinese OCR"):
        lab._fixture_snapshot(root, trusted_uid=root.stat().st_uid)
