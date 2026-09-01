# Plugin Migration Matrix

This matrix is the central release contract for Codex-format plugins copied
into the Echo plugin catalog. It complements per-plugin `MIGRATION.md`,
`CHANGELOG.md`, or `README.md` files. A plugin is migration-ready when local
smoke metadata passes, at least one runtime surface is declared, permission
review state is explicit, and either this matrix or plugin-local notes cover
the release.

Regression coverage for this matrix is centralized in
`tests/test_codex_plugin_smoke.py`, `tests/test_app_meta_endpoints.py`, and
`tests/test_apps_router.py`. Those tests verify discovery, smoke summaries,
permission-review visibility, runtime/app surfaces, public assets, and the
migration readiness endpoint.

| Plugin | Version | Runtime surfaces | Permission status | Evidence | Release status |
| --- | --- | --- | --- | --- | --- |
| `airtable` | 0.1.3 | capabilities, skills, apps | review_required | Smoke summary + permission review + central regression tests | Ready with review gate |
| `amplitude` | 1.0.2 | apps | review_required | Smoke summary + permission review + central regression tests | Ready with review gate |
| `apollo` | 1.0.2 | apps | review_required | Smoke summary + permission review + central regression tests | Ready with review gate |
| `biorender` | 1.0.2 | apps | review_required | Smoke summary + permission review + central regression tests | Ready with review gate |
| `browser` | 26.616.32156 | capabilities, skills | review_required | Smoke summary + permission review + central regression tests | Ready with review gate |
| `canva` | 1.0.2 | skills, apps | review_required | Smoke summary + permission review + central regression tests | Ready with review gate |
| `chrome` | 26.616.32156 | capabilities, skills | review_required | Smoke summary + permission review + central regression tests | Ready with review gate |
| `circleci` | 1.0.4 | capabilities, skills | review_required | Smoke summary + permission review + central regression tests | Ready with review gate |
| `clickup` | 1.0.3 | apps | review_required | Smoke summary + permission review + central regression tests | Ready with review gate |
| `coderabbit` | 1.1.4 | capabilities, skills | review_required | Smoke summary + permission review + central regression tests | Ready with review gate |
| `computer-use` | 1.0.829 | skills, mcp | review_required | Smoke summary + permission review + central regression tests | Ready with review gate |
| `datadog` | 0.1.2 | capabilities, apps | review_required | Smoke summary + permission review + central regression tests | Ready with review gate |
| `deepnote` | 0.1.5 | capabilities, skills, apps | review_required | Smoke summary + permission review + central regression tests | Ready with review gate |
| `expo` | 1.0.2 | capabilities, skills, commands | review_required | Smoke summary + permission review + central regression tests | Ready with review gate |
| `figma` | 2.0.10 | capabilities, skills, apps, commands | review_required | Smoke summary + permission review + central regression tests | Ready with review gate |
| `fireflies` | 1.0.2 | apps | review_required | Smoke summary + permission review + central regression tests | Ready with review gate |
| `github` | 0.1.5 | capabilities, skills, apps | review_required | Smoke summary + permission review + central regression tests | Ready with review gate |
| `gmail` | 0.1.3 | capabilities, skills, apps | review_required | Smoke summary + permission review + central regression tests | Ready with review gate |
| `google-calendar` | 1.2.3 | capabilities, skills, apps | review_required | Smoke summary + permission review + central regression tests | Ready with review gate |
| `google-drive` | 0.1.7 | capabilities, skills, apps | review_required | Smoke summary + permission review + central regression tests | Ready with review gate |
| `hex` | 0.1.0 | capabilities, skills, apps | review_required | Smoke summary + permission review + central regression tests | Ready with review gate |
| `hubspot` | 2.0.3 | capabilities, skills, apps | review_required | Smoke summary + permission review + central regression tests | Ready with review gate |
| `hugging-face` | 1.0.3 | capabilities, skills, apps | review_required | Smoke summary + permission review + central regression tests | Ready with review gate |
| `linear` | 0.0.2 | skills, apps | review_required | Smoke summary + permission review + central regression tests | Ready with review gate |
| `mixpanel` | 2.0.3 | capabilities, apps | review_required | Smoke summary + permission review + central regression tests | Ready with review gate |
| `motherduck` | 1.0.2 | apps | review_required | Smoke summary + permission review + central regression tests | Ready with review gate |
| `neon-postgres` | 1.0.3 | capabilities, skills, apps | review_required | Smoke summary + permission review + central regression tests | Ready with review gate |
| `netlify` | 1.1.2 | capabilities, skills, apps | review_required | Smoke summary + permission review + central regression tests | Ready with review gate |
| `notion` | 0.1.3 | capabilities, skills, apps | review_required | Smoke summary + permission review + central regression tests | Ready with review gate |
| `outlook-calendar` | 0.1.3 | capabilities, skills, apps | review_required | Smoke summary + permission review + central regression tests | Ready with review gate |
| `outlook-email` | 0.1.3 | capabilities, skills, apps | review_required | Smoke summary + permission review + central regression tests | Ready with review gate |
| `posthog` | 0.1.2 | capabilities, skills, apps | review_required | Smoke summary + permission review + central regression tests | Ready with review gate |
| `product-design` | 0.1.46 | capabilities, skills | review_required | Smoke summary + permission review + central regression tests | Ready with review gate |
| `remotion` | 1.0.3 | capabilities, skills | review_required | Smoke summary + permission review + central regression tests | Ready with review gate |
| `render` | 0.1.3 | capabilities, skills | review_required | Smoke summary + permission review + central regression tests | Ready with review gate |
| `semrush` | 1.0.3 | apps | review_required | Smoke summary + permission review + central regression tests | Ready with review gate |
| `sentry` | 0.1.2 | capabilities, skills | review_required | Smoke summary + permission review + central regression tests | Ready with review gate |
| `sharepoint` | 0.1.3 | capabilities, skills, apps | review_required | Smoke summary + permission review + central regression tests | Ready with review gate |
| `shopify` | 1.3.3 | capabilities, skills, apps | review_required | Smoke summary + permission review + central regression tests | Ready with review gate |
| `slack` | 0.1.2 | capabilities, skills, apps | review_required | Smoke summary + permission review + central regression tests | Ready with review gate |
| `stripe` | 1.0.2 | skills, apps | review_required | Smoke summary + permission review + central regression tests | Ready with review gate |
| `supabase` | 0.1.10 | capabilities, skills, apps | review_required | Smoke summary + permission review + central regression tests | Ready with review gate |
| `teams` | 0.1.3 | capabilities, skills, apps | review_required | Smoke summary + permission review + central regression tests | Ready with review gate |
| `temporal` | 0.2.2 | capabilities, skills | review_required | Smoke summary + permission review + central regression tests | Ready with review gate |
| `vercel` | 0.21.3 | capabilities, skills, apps, commands | review_required | Smoke summary + permission review + central regression tests | Ready with review gate |
| `zotero` | 0.1.2 | capabilities, skills | review_required | Smoke summary + permission review + central regression tests | Ready with review gate |

Permission status `review_required` is acceptable for migration readiness only
when the operator-facing permission rule draft remains visible. It is not the
same as unattended production enablement.

