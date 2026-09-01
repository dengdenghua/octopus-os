# Plugin Author Migration Guide

This guide is the stable handoff for third-party or local plugin authors who
want a plugin to stay compatible with the Echo operator runtime.

## Compatibility Contract

A plugin should declare the surfaces it exposes and keep those surfaces stable
across releases:

- Skills must include a clear description and any required setup.
- MCP tools must document side effects and expected inputs.
- App surfaces must name the operator workflow they support.
- Lifecycle hooks must be auditable and testable.

The smoke summary in `/api/plugins/smoke-summary` is the first compatibility
gate. A plugin can be usable with a `review_required` verdict, but it must not be
silently treated as production-ready.

## Migration Steps

When migrating a plugin between releases:

1. Run local smoke checks before enabling the plugin for operators.
2. Resolve missing capability, malformed manifest, and stale hook warnings.
3. Review inferred permissions and convert them into explicit permissions when
   possible.
4. Add or update regression tests for the operator workflow the plugin enables.
5. Record unresolved compatibility warnings as accepted risk before release.
6. For public distribution, sign the content digest with an Ed25519 publisher
   key and have the operator trust that key explicitly.
7. Exercise a transactional install and lifecycle rollback before release; an
   upgrade without migration notes and regression evidence is rejected by the
   migration gate.

## Publisher Provenance

Content hashing alone does not prove who published a plugin. Echo therefore
keeps publisher trust outside the plugin and verifies
`.codex-plugin/provenance.json` against an operator-controlled trust store.
Set `ECHO_PLUGIN_PUBLISHER_TRUST_STORE` to the trust-store path, or place the
store at `.echo/plugin-publishers.json`. The store uses schema
`echo.plugin_publisher_trust_store.v1`; each publisher has one or more
Ed25519 keys with a stable `key_id` and an `active` or `revoked` status.

The signed canonical payload binds the plugin ID, version, content digest,
publisher ID, and key ID. Any runtime-file change, manifest identity change,
unknown key, or revoked key fails the smoke gate. The signature envelope itself
is excluded from the content digest to avoid a circular hash.

Publisher keys have a complete operator-managed lifecycle. Use
`GET /api/plugins/publisher-trust` to inspect fingerprints, active-key coverage,
and 90-day key rotation warnings. Use the confirmed
`POST /api/plugins/publisher-trust/rotate` operation to add a new public key and
retire its predecessor atomically. Use the confirmed
`POST /api/plugins/publisher-trust/revoke` operation for compromise response.
Both changes are appended to the tamper-evident governance audit chain. Private
keys never enter the runtime or its audit records.

## Permission Review

Permission review is mandatory for plugins that execute tools, invoke MCP
servers, read or write local files, or register lifecycle hooks.

The review should answer:

- Which permission is required?
- Which operator action triggers it?
- Can the permission be narrowed?
- Is there replay or audit evidence for the workflow?

If a plugin has inferred permissions, treat them as review-required until a
human accepts the risk or the manifest is updated.

## Release Checklist

Before release, confirm:

- Compatibility smoke checks pass or show explicit review-required rows.
- Permission review status is visible to the operator.
- Migration notes describe breaking changes and required operator action.
- Hook behavior is covered by regression tests.
- The plugin can be disabled without corrupting memory, replay, or governance
  audit records.
- Publicly distributed plugins have a trusted publisher signature; unsigned
  local plugins remain visibly review-required.
- Publisher key rotation and revocation have been exercised from the operator
  panel, and the resulting governance audit entries contain no private signing
  material.

