#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

run_backend=1
run_frontend_static=1
run_frontend_unit="${ECHO_VERIFY_SKIP_FRONTEND_UNIT:-0}"
run_frontend_build="${ECHO_VERIFY_SKIP_BUILD:-0}"
run_full_stack="${ECHO_VERIFY_SKIP_FULL_STACK:-0}"
run_full_stack_mobile="${ECHO_VERIFY_SKIP_FULL_STACK_MOBILE:-0}"
run_production_gate="${ECHO_VERIFY_SKIP_PRODUCTION_GATE:-0}"
VERIFY_STATE_ROOT="${ECHO_VERIFY_STATE_ROOT:-$ROOT_DIR/test-results/local-verify-state}"
VERIFY_DATA_DIR="$VERIFY_STATE_ROOT/data"
VERIFY_REVIEW_QUEUE="$VERIFY_DATA_DIR/review_queue.json"
VERIFY_READINESS_REPORT="$VERIFY_STATE_ROOT/production_readiness_gate.json"
VERIFY_FULL_STACK_PROOF="$VERIFY_STATE_ROOT/full_stack_smoke_proof.json"
VERIFY_E2E_RELEASE_PROOF="$VERIFY_STATE_ROOT/e2e_release_proof.json"

usage() {
  cat <<'EOF'
Usage: scripts/verify_local.sh [--full-stack-only]

Runs the local stability gate:
  - targeted backend regressions for model compatibility, team/cowork tasks, and org runs
  - production readiness scorecard gate for operator, automation, and policy evidence
  - focused frontend production unit tests for runtime-critical UI contracts
  - frontend typecheck, lint, and build
  - full-stack Playwright smoke for FastAPI + Vite across localhost/127.0.0.1
  - mobile full-stack Playwright smoke for core workspace responsive paths

Environment:
  PYTHON                         Python executable. Defaults to .venv/bin/python, then python3/python.
  ECHO_VERIFY_SKIP_FRONTEND_UNIT=1
                                  Skip focused frontend production unit tests.
  ECHO_VERIFY_SKIP_BUILD=1     Skip frontend production build.
  ECHO_VERIFY_SKIP_PRODUCTION_GATE=1
                                  Skip production readiness scorecard gate.
  ECHO_VERIFY_SKIP_FULL_STACK=1 Skip full-stack Playwright smoke.
  ECHO_VERIFY_SKIP_FULL_STACK_MOBILE=1
                                  Skip mobile full-stack Playwright smoke.
  ECHO_VERIFY_STATE_ROOT       Isolated runtime state root for local gates.
                                  Defaults to test-results/local-verify-state.
  ECHO_LIVE_MODEL_SMOKE=1      Also run live OpenAI-compatible provider smoke tests.
  ECHO_TTFT_LIVE_SMOKE=1       Also run the live TTFT streaming gate (boots a
                                  real server and drives WS turns against a live
                                  model; needs config.local.yaml + provider keys).
EOF
}

for arg in "$@"; do
  case "$arg" in
    --full-stack-only)
      run_backend=0
      run_frontend_static=0
      run_frontend_build=1
      run_full_stack=0
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "unknown argument: $arg" >&2
      usage >&2
      exit 2
      ;;
  esac
done

resolve_python() {
  if [[ -n "${PYTHON:-}" ]]; then
    printf '%s\n' "$PYTHON"
    return
  fi
  if [[ -x "$ROOT_DIR/.venv/bin/python" ]]; then
    printf '%s\n' "$ROOT_DIR/.venv/bin/python"
    return
  fi
  if command -v python3 >/dev/null 2>&1; then
    command -v python3
    return
  fi
  command -v python
}

section() {
  printf '\n==> %s\n' "$1"
}

PYTHON_BIN="$(resolve_python)"
export PYTHON="$PYTHON_BIN"
mkdir -p "$VERIFY_DATA_DIR"
VERIFY_STATE_ROOT_ABS="$(cd "$VERIFY_STATE_ROOT" && pwd)"
VERIFY_RUN_ID="${ECHO_VERIFY_RUN_ID:-$(date -u +%Y%m%dT%H%M%SZ)-$$}"
VERIFY_DESKTOP_PLAYWRIGHT_REPORT="$VERIFY_STATE_ROOT_ABS/full-stack-playwright-report.json"
VERIFY_MOBILE_PLAYWRIGHT_REPORT="$VERIFY_STATE_ROOT_ABS/full-stack-mobile-playwright-report.json"

backend_tests=(
  tests/test_openapi_snapshot.py
  tests/test_openai_router.py
  tests/test_openai_compat_providers.py
  tests/test_app_config_endpoints.py::TestCustomModelCompatDiagnostics
  tests/test_organization.py
  tests/test_team_tasks_router.py
  tests/test_cowork_group.py
  tests/test_cowork_group_store.py
  tests/test_cowork_group_router.py
  tests/test_cowork_search.py
  tests/test_cowork_turn_plan.py
  tests/test_cowork_advanced.py
  tests/test_e2e_smoke_proof.py
  tests/test_e2e_release_proof.py
  tests/test_production_readiness_gate.py
)

if [[ "${ECHO_LIVE_MODEL_SMOKE:-0}" == "1" ]]; then
  backend_tests+=(tests/test_openai_compat_provider_smoke.py)
fi

if [[ "${ECHO_TTFT_LIVE_SMOKE:-0}" == "1" ]]; then
  backend_tests+=(tests/test_ttft_live_smoke.py)
fi

if [[ "$run_backend" == "1" ]]; then
  section "backend targeted stability tests"
  "$PYTHON_BIN" -m pytest "${backend_tests[@]}" -q
fi

if [[ "$run_production_gate" != "1" ]]; then
  section "production readiness gate"
  ECHO_HOME="$VERIFY_STATE_ROOT" \
  ECHO_DATA_DIR="$VERIFY_DATA_DIR" \
  "$PYTHON_BIN" scripts/production_readiness_gate.py \
    --review-queue-path "$VERIFY_REVIEW_QUEUE" \
    --json-output "$VERIFY_READINESS_REPORT"
  echo "readiness report: $VERIFY_READINESS_REPORT"
fi

if [[ "$run_frontend_static" == "1" ]]; then
  section "frontend typecheck"
  (cd frontend && pnpm typecheck)

  section "frontend lint"
  (cd frontend && pnpm lint)

  if [[ "$run_frontend_unit" != "1" ]]; then
    section "frontend production unit tests"
    (cd frontend && pnpm exec vitest run \
      src/core/i18n/translations.test.ts \
      src/components/workspace/settings/appearance-settings-page.test.tsx \
      src/components/workspace/rec-recorder-overlay.smoke.test.tsx)
  fi
fi

if [[ "$run_frontend_build" != "1" ]]; then
  section "frontend build"
  (cd frontend && pnpm build)
fi

if [[ "$run_full_stack" != "1" ]]; then
  section "full-stack smoke"
  (
    cd frontend
    PYTHON="$PYTHON_BIN" \
    ECHO_E2E_STATE_ROOT="$VERIFY_STATE_ROOT_ABS/full-stack" \
    ECHO_E2E_JSON_REPORT="$VERIFY_DESKTOP_PLAYWRIGHT_REPORT" \
    pnpm e2e:full
  )
  "$PYTHON_BIN" scripts/e2e_smoke_proof.py \
    --output "$VERIFY_FULL_STACK_PROOF" \
    --suite full-stack-desktop \
    --status passed \
    --state-root "$VERIFY_STATE_ROOT_ABS/full-stack" \
    --frontend-port "${FRONTEND_PORT:-13000}" \
    --backend-host "${GATEWAY_HOST:-127.0.0.1}" \
    --backend-port "${GATEWAY_PORT:-18000}" \
    --playwright-report "$VERIFY_DESKTOP_PLAYWRIGHT_REPORT" \
    --run-id "$VERIFY_RUN_ID" \
    --test-match "full-stack-smoke.spec.ts,chat.spec.ts,regression.spec.ts,workflow-editor.spec.ts"

  if [[ "$run_full_stack_mobile" != "1" ]]; then
    section "mobile full-stack smoke"
    (
      cd frontend
      PYTHON="$PYTHON_BIN" \
      ECHO_E2E_STATE_ROOT="$VERIFY_STATE_ROOT_ABS/full-stack-mobile" \
      ECHO_E2E_JSON_REPORT="$VERIFY_MOBILE_PLAYWRIGHT_REPORT" \
      pnpm e2e:full:mobile
    )
    "$PYTHON_BIN" scripts/e2e_smoke_proof.py \
      --output "$VERIFY_FULL_STACK_PROOF" \
      --suite full-stack-mobile \
      --status passed \
      --state-root "$VERIFY_STATE_ROOT_ABS/full-stack-mobile" \
      --frontend-port "${FRONTEND_PORT:-13000}" \
      --backend-host "${GATEWAY_HOST:-127.0.0.1}" \
      --backend-port "${GATEWAY_PORT:-18000}" \
      --playwright-report "$VERIFY_MOBILE_PLAYWRIGHT_REPORT" \
      --run-id "$VERIFY_RUN_ID" \
      --test-match "mobile-smoke.spec.ts"
  fi
  echo "full-stack smoke proof: $VERIFY_FULL_STACK_PROOF"
  if [[ -f "$VERIFY_READINESS_REPORT" ]]; then
    release_proof_args=(
      scripts/e2e_release_proof.py
      --readiness "$VERIFY_READINESS_REPORT"
      --full-stack "$VERIFY_FULL_STACK_PROOF"
      --output "$VERIFY_E2E_RELEASE_PROOF"
      --required-suite full-stack-desktop
    )
    if [[ "$run_full_stack_mobile" != "1" ]]; then
      release_proof_args+=(--required-suite full-stack-mobile)
    fi
    "$PYTHON_BIN" "${release_proof_args[@]}"
    echo "e2e release proof: $VERIFY_E2E_RELEASE_PROOF"
  else
    echo "e2e release proof skipped: readiness report missing"
  fi
fi

