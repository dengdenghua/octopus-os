/**
 * E2E: remote workspace collaboration feature.
 *
 * Covers Task 13.2 — multi-user remote workspace collaboration flows:
 *   - WorkspaceSwitcher list / search / switch
 *   - MountPointDialog add mount point (protocol / credentials / test)
 *   - WorkspaceMembersPanel member list + role management
 *   - FileLeaseIndicator locked-file badge (holder + remaining time)
 *   - Two-user collaboration: file tree real-time sync, lease indicator
 *
 * Environment requirements
 * ------------------------
 * The full collaboration scenarios (skipped describe blocks below)
 * require:
 *
 *   1. Backend (FastAPI) running on `GATEWAY_PORT` (default 18000) with
 *      `ui.remote_workspace` feature flag enabled. Without the flag the
 *      `/api/workspaces` endpoints return 404 and the WorkspaceSwitcher
 *      shows "Failed to load workspaces".
 *   2. A seeded workspace owned by `alice` with at least one `editor`
 *      member (`bob`) and one `viewer` member (`dave`). Today there is
 *      no e2e fixture that creates this state — `full-stack-smoke.spec.ts`
 *      only seeds a realtime thread, not workspace rows.
 *   3. Two authenticated browser contexts (one per user) so the test can
 *      exercise concurrent editing + lease conflicts. `fixtures.ts` only
 *      wires page-error/console assertions; it does not provide a
 *      `userContext` fixture. Adding one requires an auth setup step
 *      (login or token injection) that the backend doesn't yet expose
 *      for e2e.
 *   4. A live cowork bridge so `file_written` events broadcast to other
 *      online members. This needs a real WebSocket connection per user,
 *      which the current Vite dev server proxies to the backend.
 *
 * Until those fixtures land, only the smoke test at the top runs. The
 * remaining tests are skipped via `test.describe.skip` with a comment
 * pointing at the missing piece.
 *
 * When the fixtures are ready, flip each `describe.skip` to `describe`
 * and remove the corresponding comment.
 */

import { expect, test } from "./fixtures";

test.describe("Workspace collaboration smoke", () => {
  test("workspace switcher trigger is visible in the sidebar", async ({
    authedPage: page,
  }) => {
    // No backend required — the trigger button is always rendered,
    // even when the workspace list fails to load.
    await page.goto("/");
    await page.waitForLoadState("domcontentloaded");

    const switcher = page.getByRole("button", {
      name: "Switch workspace",
      exact: true,
    });
    await expect(switcher).toBeVisible({ timeout: 15_000 });
  });
});

// Skipped: the "Add workspace" button only renders after the switcher
// panel opens AND the workspace list has loaded (even an empty list).
// Without `ui.remote_workspace` enabled on the backend, /api/workspaces
// returns 404 and the panel stays in the "Failed to load workspaces"
// state, so the Add button never appears. Enable the feature flag on
// the backend to unskip.
test.describe.skip(
  "Workspace switcher opens mount-point dialog",
  () => {
    test("Add workspace button opens MountPointDialog", async ({ page }) => {
      await page.goto("/");
      await page.waitForLoadState("domcontentloaded");

      const switcher = page.getByRole("button", {
        name: "Switch workspace",
        exact: true,
      });
      await switcher.click();

      const addBtn = page.getByRole("button", { name: "Add workspace" });
      await expect(addBtn).toBeVisible({ timeout: 5_000 });
      await addBtn.click();

      // MountPointDialog should appear with the protocol picker.
      await expect(
        page.getByRole("heading", { name: "Add workspace" }),
      ).toBeVisible({ timeout: 5_000 });
    });
  },
);

// Skipped: requires backend with `ui.remote_workspace` feature flag
// enabled so /api/workspaces returns 200 and the MountPointDialog can
// POST a new workspace. The Vite dev proxy also needs the backend live
// on GATEWAY_PORT (default 18000).
test.describe.skip(
  "Workspace registration via MountPointDialog",
  () => {
    test("register a local-mount workspace end-to-end", async ({ page }) => {
      // Full flow:
      //   1. Open WorkspaceSwitcher → click "Add workspace"
      //   2. In MountPointDialog: pick protocol=local, enter name + path
      //   3. Click "Test connection" → expect "Connection OK"
      //   4. Click "Create" → dialog closes, new workspace appears in list
      //   5. GET /api/workspaces returns the new workspace
      //
      // Requires:
      //   - Backend /api/workspaces POST wired to WorkspaceStore
      //   - A writable local path (e.g. /tmp/e2e-workspace-<ts>)
      //   - feature flag ui.remote_workspace = true
      await page.goto("/");
      await page.waitForLoadState("domcontentloaded");

      const switcher = page.getByRole("button", {
        name: "Switch workspace",
        exact: true,
      });
      await switcher.click();
      await page.getByRole("button", { name: "Add workspace" }).click();

      await page.getByLabel("Name").fill("E2E Local Workspace");
      await page.getByLabel("Path").fill("/tmp/e2e-workspace");

      const testBtn = page.getByRole("button", { name: "Test connection" });
      await testBtn.click();
      await expect(page.getByText("Connection OK")).toBeVisible({
        timeout: 10_000,
      });

      await page.getByRole("button", { name: "Create" }).click();

      // Dialog closes.
      await expect(
        page.getByRole("heading", { name: "Add workspace" }),
      ).not.toBeVisible({ timeout: 5_000 });

      // The new workspace shows up in the switcher list.
      await switcher.click();
      await expect(
        page.getByRole("button", { name: "Switch to E2E Local Workspace" }),
      ).toBeVisible({ timeout: 5_000 });
    });

    test("register an S3-mount workspace with credentials", async ({
      page,
    }) => {
      // Verifies the protocol picker swaps fields to S3 shape
      // (endpoint / bucket / access key / secret key) and that the
      // credentials hint is shown. Requires the S3MountBackend adapter
      // to be importable on the backend and (ideally) a MinIO test
      // container as the connection target.
      await page.goto("/");
      await page.waitForLoadState("domcontentloaded");
      await page
        .getByRole("button", { name: "Switch workspace", exact: true })
        .click();
      await page.getByRole("button", { name: "Add workspace" }).click();

      // Pick S3 protocol.
      await page.getByRole("button", { name: "S3" }).click();

      // S3-specific fields render.
      await expect(page.getByLabel("Endpoint URL")).toBeVisible();
      await expect(page.getByLabel("Bucket")).toBeVisible();
      await expect(page.getByLabel("Access key")).toBeVisible();
      await expect(page.getByLabel("Secret key")).toBeVisible();

      // Credentials hint is visible (reassures user secrets stay on backend).
      await expect(
        page.getByText(/Credentials are sent to the backend only/),
      ).toBeVisible();
    });
  },
);

// Skipped: requires a seeded workspace + auth context fixture. Today
// fixtures.ts has no `userContext` helper; adding one needs
// /api/auth/test-login or similar. The WorkspaceMembersPanel also
// needs a stable route (currently it lives inside workspace-sidebar).
test.describe.skip("Workspace member management", () => {
  test("owner can add members and change roles", async ({ page }) => {
    // Pre-conditions:
    //   - Backend has a workspace `ws-e2e` owned by the logged-in user
    //     (alice). Seeded via a fixture that POSTs to /api/workspaces.
    //   - Auth context for alice is established (cookie or token).
    //
    // Flow:
    //   1. Open WorkspaceMembersPanel for `ws-e2e`
    //   2. Click "Add member", enter `bob`, pick role=Editor, submit
    //   3. Verify bob appears in the member list with "Editor" badge
    //   4. Open bob's role dropdown, change to Reviewer
    //   5. Verify badge updates to "Reviewer"
    //   6. GET /api/workspaces/ws-e2e/members returns the updated role
    await page.goto("/");
    await page.waitForLoadState("domcontentloaded");
    // TODO: navigate to workspace members panel once the route is stable
    // (the panel currently lives inside workspace-sidebar; a dedicated
    // route /#/workspace/members may be added later).
  });

  test("non-owner cannot change roles", async ({ page }) => {
    // Requires a second auth context (bob). Today fixtures.ts has no
    // `userContext` helper; adding one needs /api/auth/test-login or
    // similar. Skipped until that lands.
    await page.goto("/");
    await page.waitForLoadState("domcontentloaded");
  });
});

// Skipped: requires a seeded lease + workspace mount. The lease would
// be acquired via POST /api/workspaces/ws-e2e/lease in a fixture
// (before the page loads), so the FileLeaseIndicator renders with
// "Locked by bob" on page open.
test.describe.skip("File lease indicator", () => {
  test("locked file shows holder avatar + remaining time", async ({
    page,
  }) => {
    // Pre-conditions:
    //   - Workspace `ws-e2e` exists with a file `config.yaml`
    //   - A lease is acquired by `bob` via POST /api/workspaces/ws-e2e/lease
    //     (fixture-driven, not UI-driven, so the lease is active when
    //     the page loads)
    //
    // Flow:
    //   1. Open the file tree for `ws-e2e`
    //   2. Locate `config.yaml` row
    //   3. Expect the FileLeaseIndicator to be visible with
    //      aria-label="Locked" + a tooltip showing "Locked by bob"
    //   4. Hover → "Request takeover" button appears
    await page.goto("/");
    await page.waitForLoadState("domcontentloaded");
  });
});

// Skipped: requires two browser contexts (alice + bob) each with auth
// cookies, plus a live cowork WebSocket so `file_written` events
// broadcast to other online members. The Vite dev proxy must forward
// /ws to the backend.
test.describe.skip("Two-user collaboration", () => {
  test("file tree updates in real-time when the other user writes", async ({
    page,
    browser,
  }) => {
    // Pre-conditions:
    //   - Two authenticated contexts: alice (owner) and bob (editor)
    //   - Both connected to the same cowork WebSocket for `ws-e2e`
    //   - Backend ui.remote_workspace = true
    //
    // Flow:
    //   1. Alice opens workspace `ws-e2e` in context A
    //   2. Bob opens the same workspace in context B
    //   3. Bob acquires a lease on `notes.md` + writes content
    //   4. Alice's file tree shows `notes.md` appearing within 2s
    //      (cowork `file_written` event → tree refetch)
    //   5. Alice's file tree shows the lease indicator on `notes.md`
    //      with "Locked by bob"
    //
    // Requires:
    //   - `browser.newContext()` per user with auth cookies set
    //   - Real cowork WebSocket (not mocked) — Vite proxy must forward
    //     /ws to the backend
    const bobContext = await browser.newContext();
    try {
      const bobPage = await bobContext.newPage();
      await page.goto("/");
      await bobPage.goto("/");
      await page.waitForLoadState("domcontentloaded");
      await bobPage.waitForLoadState("domcontentloaded");
      // TODO: implement once auth context fixture is available.
    } finally {
      await bobContext.close();
    }
  });

  test("lease conflict shown when second user tries to write", async ({
    page,
    browser,
  }) => {
    // Pre-conditions: same as above.
    //
    // Flow:
    //   1. Alice acquires lease on `config.yaml`
    //   2. Bob opens the same file → FileLeaseIndicator shows "Locked by alice"
    //   3. Bob clicks "Edit anyway" → server returns 409
    //   4. UI shows conflict toast "alice is editing, Nm left"
    //   5. Bob clicks "Request takeover" → alice sees a takeover request
    //
    // Asserts both the 409 from /api/fs/write and the UI toast.
    const bobContext = await browser.newContext();
    try {
      const bobPage = await bobContext.newPage();
      await page.goto("/");
      await bobPage.goto("/");
      await page.waitForLoadState("domcontentloaded");
      await bobPage.waitForLoadState("domcontentloaded");
      // TODO: implement once cowork WebSocket + takeover request channel land.
    } finally {
      await bobContext.close();
    }
  });
});

// Skipped: requires viewer auth context + seeded workspace. The viewer
// (dave) needs to be added as a member with role=viewer on `ws-e2e`,
// and the test asserts both the UI (no edit affordance) and the API
// (/api/fs/write returns 403 with detail.error="write_requires_editor").
test.describe.skip("ACL enforcement in UI", () => {
  test("viewer role sees read-only file tree", async ({ page }) => {
    // Pre-conditions:
    //   - dave is a viewer on `ws-e2e`
    //   - dave's auth context is established
    //
    // Flow:
    //   1. Open `ws-e2e` as dave
    //   2. File tree rows show no "edit" affordance
    //   3. Attempting /api/fs/write returns 403 (asserted via
    //      page.request.post) with detail.error="write_requires_editor"
    await page.goto("/");
    await page.waitForLoadState("domcontentloaded");
  });
});
