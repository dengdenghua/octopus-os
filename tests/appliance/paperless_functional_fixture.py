from __future__ import annotations

import hashlib
from collections.abc import Mapping
from typing import Any

from deploy.appliance import paperless_functional_lab as lab
from tests.appliance.hub_lifecycle_fixture import (
    _installation,
    hub_lifecycle_material,
)


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def paperless_functional_material(
    candidate: Mapping[str, Any],
    *,
    architecture: str = "amd64",
) -> tuple[bytes, bytes]:
    hub_plan_raw, _hub_result_raw = hub_lifecycle_material(
        candidate,
        architecture=architecture,
    )
    hub_plan = lab._strict_json_value(hub_plan_raw, "Hub fixture plan")
    catalog = hub_plan["catalog"]
    release = hub_plan["releaseCandidate"]
    operations = {
        **hub_plan["operationsBundle"],
        "paperlessLabSha256": "d" * 64,
        "paperlessLabSize": 24576,
    }
    fixtures = []
    names = {
        "ocr-zh": "ocr-zh.pdf",
        "ocr-en": "ocr-en.pdf",
        "office-docx": "office.docx",
        "office-xlsx": "office.xlsx",
        "office-pptx": "office.pptx",
    }
    for index, fixture_id in enumerate(lab.FIXTURE_IDS, start=1):
        contract = lab.FIXTURE_CONTRACTS[fixture_id]
        fixtures.append(
            {
                "id": fixture_id,
                "file": names[fixture_id],
                "coverage": contract["coverage"],
                "mediaType": contract["mediaType"],
                "size": 1024 + index,
                "sha256": _sha256(f"paperless-source:{architecture}:{fixture_id}"),
                "searchTermSha256": _sha256(
                    f"paperless-private-search:{architecture}:{fixture_id}"
                ),
            }
        )
    identity = {
        "schemaVersion": lab.SCHEMA_VERSION,
        "kind": lab.PLAN_KIND,
        "baseUrl": "http://127.0.0.1:3008",
        "releaseCandidate": release,
        "operationsBundle": operations,
        "runtime": hub_plan["runtime"],
        "catalog": catalog,
        "installation": _installation(
            lab.APP_ID,
            catalog["apps"][lab.APP_ID],
            cycle=3,
        ),
        "fixtures": fixtures,
        "workflow": ["authenticate", "upload", "task", "search", "export-original", "cleanup"],
    }
    plan_id = hashlib.sha256(lab._canonical(identity)).hexdigest()
    plan = {
        **identity,
        "planId": plan_id,
        "confirmation": f"RUN ECHO PAPERLESS FUNCTIONAL LAB {plan_id}",
    }
    records = {
        fixture["id"]: {
            "sourceSha256": fixture["sha256"],
            "sourceBytes": fixture["size"],
            "taskIdSha256": _sha256(f"task:{architecture}:{fixture['id']}"),
            "taskStatus": "success",
            "documentIdSha256": _sha256(f"document:{architecture}:{fixture['id']}"),
            "searchTermSha256": fixture["searchTermSha256"],
            "searchMatched": True,
            "originalDownloadSha256": fixture["sha256"],
            "originalDownloadBytes": fixture["size"],
            "cleanupStatus": 204,
        }
        for fixture in fixtures
    }
    result: dict[str, Any] = {
        "schemaVersion": lab.SCHEMA_VERSION,
        "kind": lab.RESULT_KIND,
        "planId": plan_id,
        "releaseCandidate": release,
        "operationsBundle": operations,
        "catalogDigest": catalog["digest"],
        "architecture": catalog["architecture"],
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
        "completedAtUnix": 1_700_000_000,
    }
    result["resultId"] = hashlib.sha256(lab._canonical(result)).hexdigest()
    plan_raw = lab._canonical(plan)
    result_raw = lab._canonical(result)
    lab.validate_evidence_bytes(
        plan_raw,
        result_raw,
        expected_candidate=release,
        now=1_700_000_000,
    )
    return plan_raw, result_raw
