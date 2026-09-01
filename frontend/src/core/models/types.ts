import type { ReasoningEffort } from "@/core/threads";

export interface Model {
  id: string;
  name: string;
  /** Concrete wire-model id. Built-in virtual models may omit it. */
  model?: string | null;
  display_name: string;
  /** Human-facing source entry, e.g. "OpenCode Zen 免费模型". */
  source_display_name?: string | null;
  description?: string | null;
  /** Stable custom-model entry key; distinct endpoints may share `model`. */
  entry_id?: string | null;
  /** Stable row-level route key: endpoint + variant + context profile. */
  selection_id?: string | null;
  supports_thinking?: boolean;
  supports_vision?: boolean;
  supports_tool_use?: boolean;
  supports_reasoning_effort?: boolean;
  reasoning_efforts?: ReasoningEffort[] | null;
  context_window?: number | null;
  context_profile?: string | null;
  // Provider identification · used to look up ProviderCapabilities for
  // UI gating (e.g. grey out "upload image" for vision-less providers,
  // show a cache-hit badge for cache-supported ones).
  provider?: string;
  [key: string]: unknown;
}

/**
 * Provider capabilities · mirrors backend
 * `runtime/sensing/eyes/provider.py::ProviderCapabilities`. Fetched
 * from `GET /api/providers` and cached via `useProviders()`.
 *
 * The type is re-exported from the auto-generated OpenAPI bundle
 * (see docs/adr/004-openapi-ts-codegen.md). Pre-codegen this was a
 * hand-written interface that had ``default_model: string`` /
 * ``pricing_hint: string`` · both are actually ``string | null``
 * on the backend (FastAPI serializes ``None`` → ``null``). Using the
 * generated type closes that drift; any caller that assumed
 * non-null now gets a TS error where it matters.
 */
import type { components } from "@/core/api/openapi-types";

export type ProviderCapabilities =
  components["schemas"]["ProviderCapabilitiesWire"];
