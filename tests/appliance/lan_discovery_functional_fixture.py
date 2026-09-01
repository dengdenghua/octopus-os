from __future__ import annotations

import hashlib
from collections.abc import Mapping
from typing import Any

from deploy.appliance import lan_discovery_functional_lab as lab
from tests.appliance.hub_lifecycle_fixture import _installation, hub_lifecycle_material


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def lan_discovery_functional_material(
    candidate: Mapping[str, Any],
    *,
    architecture: str = "amd64",
) -> tuple[bytes, bytes, dict[str, bytes]]:
    hub_plan_raw, _hub_result_raw = hub_lifecycle_material(candidate, architecture=architecture)
    hub_plan = lab._strict_json(hub_plan_raw, "Hub fixture plan")
    catalog = hub_plan["catalog"]
    release = hub_plan["releaseCandidate"]
    operations = {
        **hub_plan["operationsBundle"],
        "lanDiscoveryLabSha256": "e" * 64,
        "lanDiscoveryLabSize": 32768,
    }
    identity = {
        "schemaVersion": lab.SCHEMA_VERSION,
        "kind": lab.PLAN_KIND,
        "baseUrls": {
            "syncthing": "http://127.0.0.1:3007",
            "homeAssistant": "http://127.0.0.1:8123",
        },
        "releaseCandidate": release,
        "operationsBundle": operations,
        "runtime": hub_plan["runtime"],
        "catalog": catalog,
        "installations": {
            app_id: _installation(app_id, catalog["apps"][app_id], cycle=3)
            for app_id in lab.APP_IDS
        },
        "workflow": lab.WORKFLOW,
    }
    plan_id = hashlib.sha256(lab._canonical(identity)).hexdigest()
    plan = {
        **identity,
        "planId": plan_id,
        "confirmation": f"RUN ECHO LAN DISCOVERY FUNCTIONAL LAB {plan_id}",
    }
    nas = {
        "role": "nas",
        "machineIdentitySha256": _hash(f"machine:nas:{architecture}"),
        "localDeviceSha256": _hash(f"device:nas:{architecture}"),
        "peerDeviceSha256": _hash(f"device:companion:{architecture}"),
        "configuredAddressesAreDynamic": True,
        "localDiscoveryHealthy": True,
        "peerFoundInDiscoveryCache": True,
        "connectionAddressMatchedDiscovery": True,
        "connectionType": "tcp-server",
        "connectionIsLocal": True,
        "connectionAddressPrivate": True,
        "trafficBytes": 4096,
        "clientVersionSha256": _hash("syncthing:v2.0.10"),
    }
    companion = {
        **nas,
        "role": "companion",
        "machineIdentitySha256": _hash(f"machine:companion:{architecture}"),
        "localDeviceSha256": nas["peerDeviceSha256"],
        "peerDeviceSha256": nas["localDeviceSha256"],
        "connectionType": "tcp-client",
        "trafficBytes": 8192,
    }
    zeroconf_entry = _hash(f"ha:zeroconf:{architecture}")
    ssdp_entry = _hash(f"ha:ssdp:{architecture}")
    home_assistant = {
        "discoveredEntries": [
            {
                "source": "zeroconf",
                "entryIdSha256": zeroconf_entry,
                "domainSha256": _hash("ha-domain:matter"),
                "state": "loaded",
            },
            {
                "source": "ssdp",
                "entryIdSha256": ssdp_entry,
                "domainSha256": _hash("ha-domain:hue"),
                "state": "loaded",
            },
        ],
        "control": {
            "entityIdSha256": _hash(f"ha-entity:{architecture}"),
            "domain": "switch",
            "configEntryIdSha256": zeroconf_entry,
            "source": "zeroconf",
            "initialState": "off",
            "changedState": "on",
            "restoredState": "off",
            "stateChanged": True,
            "stateRestored": True,
        },
    }
    probes = {
        lab.SYNCTHING_NAS_NAME: lab._canonical(
            {
                "schemaVersion": lab.SCHEMA_VERSION,
                "kind": lab.PROBE_KIND,
                "planId": plan_id,
                "probe": "syncthing-nas",
                "observedAtUnix": 1_700_000_000,
                "passed": True,
                "details": nas,
            }
        ),
        lab.SYNCTHING_COMPANION_NAME: lab._canonical(
            {
                "schemaVersion": lab.SCHEMA_VERSION,
                "kind": lab.PROBE_KIND,
                "planId": plan_id,
                "probe": "syncthing-companion",
                "observedAtUnix": 1_700_000_000,
                "passed": True,
                "details": companion,
            }
        ),
        lab.HOME_ASSISTANT_NAME: lab._canonical(
            {
                "schemaVersion": lab.SCHEMA_VERSION,
                "kind": lab.PROBE_KIND,
                "planId": plan_id,
                "probe": "home-assistant",
                "observedAtUnix": 1_700_000_000,
                "passed": True,
                "details": home_assistant,
            }
        ),
    }
    result: dict[str, Any] = {
        "schemaVersion": lab.SCHEMA_VERSION,
        "kind": lab.RESULT_KIND,
        "planId": plan_id,
        "releaseCandidate": release,
        "operationsBundle": operations,
        "catalogDigest": catalog["digest"],
        "architecture": catalog["architecture"],
        "syncthing": {"nas": nas, "companion": companion},
        "homeAssistant": home_assistant,
        "probeArtifacts": {
            name: {"sha256": hashlib.sha256(raw).hexdigest(), "size": len(raw)}
            for name, raw in probes.items()
        },
        "checks": {
            "syncthingLanDiscoveryVerified": True,
            "syncthingDirectLanConnectionVerified": True,
            "homeAssistantZeroconfDiscoveryVerified": True,
            "homeAssistantSsdpDiscoveryVerified": True,
            "homeAssistantReversibleControlVerified": True,
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
    lab.validate_probe_artifacts(plan, result, probes)
    return plan_raw, result_raw, probes
