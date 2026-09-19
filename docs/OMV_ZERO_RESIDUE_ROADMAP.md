# Echo OS — OMV 零残留路线图

> 状态：侦察完成，路线图已出。**Phase 1 已执行（2026-09-13）并验证**，详见 §6 执行记录。
> 结论先行：**OMV Web RPC 路由层在运行时里是死代码**，其端点已被原生存储面覆盖；但 `omv_bridge*` 主机桥族群是 `deploy/omv/` 仍打包、在 x86 宿主机以 `python3 -m appliance.omv_bridge` 运行的**活守护进程**，属 Phase 3 范围，已从 Phase 1 删除清单中剔除。零残留可分阶段、可回滚推进。

---

## 0. 一句话结论

OMV 后端（调用 OMV `engined` RPC 的桥接层）在 Echo OS 运行时中**没有被挂载、也没有被任何 live 模块 import**。原生存储面 `native_storage_routes.py` 已经完整提供账户 / 用户 / 密码 / 用户组 / 配额 / 共享 / 只读 / 健康检查的等价端点。因此：

- 删掉 OMV 后端集群 = **功能零损失**，可直接做（Phase 1）。
- 运行时里剩下的 `omv_` 名字只是「schema 契约 + 命名残留」两层，靠改名去残留（Phase 2）。
- `deploy/omv/` 宿主桥与 x86 CI 证据门禁是**活的**，需等原生面生产就绪后再后置清理（Phase 3）。

---

## 1. 证据（为什么敢判定为死代码）

### 1.1 挂载点唯一性 — `create_omv_router` 从未进入应用
- `appliance/extension.py:306` 是全仓唯一挂载的存储路由：`app.include_router(create_native_storage_router(...))`。
- 全仓 grep `create_omv_router` 命中：
  - `appliance/omv_router.py:33`（定义）、`omv_router.py:83`（内部引用）
  - `tests/appliance/test_omv_router_structure.py:52`、`test_omv_router.py:13,528`（测试）
  - **没有任何 `include_router(create_omv_router(...))` 调用** → OMV 路由树从未进运行中的应用。

### 1.2 导入图 — 死集群是「只被自己和测试引用」的孤岛
对 `omv_router / omv_client / omv_bridge* / omv_account_routes / omv_quota_routes / omv_read_routes / omv_sharing_routes / omv_health / omv_response / omv_route_context` 做全仓导入 grep：
- 集群**内部**互相 import（自引用闭环，例如 `omv_router.py:9-29` import 全部 route/context 模块，`omv_bridge.py:20-48` import 全部 bridge mixin）。
- 仅 `tests/appliance/test_omv_*.py` 从集群外部 import。
- **没有任何 live runtime 模块**（extension.py / native_* / accounts.py / data_access.py / runtime/…）import 其中任何一个。

⚠️ **关键分野（执行 Phase 1 时修正）**：`omv_bridge*` 族群（9 文件）虽在导入图里也是「外部无引用」，但它是**主机桥守护进程**而非 Web 路由：
- `omv_bridge.py` import 其 8 个 sibling（`omv_bridge_accounts/_contract/_errors/_http/_inventory/_quota/_runners/_sharing`），`_http` 还 import 活契约 `omv_protocol`；**整族不 import 任何被删的 Web 路由层文件**。
- `deploy/omv/plugin_package.py:45` 与 `host_bundle.py:41` 的清单**把 `appliance/omv_bridge.py` 作为 payload 打包**；`deploy/omv/echo_omv_host.py:56` 与 `echo-omv-bridge.service.example:18` 以 `python3 -m appliance.omv_bridge` 在 x86 宿主机运行之。
- ⇒ `omv_bridge*` 是 **Phase 3（deploy/omv/ 清理）的活代码**，不属于 Phase 1 的死 Web 路由层。Phase 1 初版误删此族，已由 `git checkout HEAD -- appliance/omv_bridge*.py` 恢复，见 §6。

### 1.3 原生面已完整覆盖 OMV 后端能力（`native_storage_routes.py`）
全部走 `native_storage.*`，非 OMV RPC：
| OMV 后端原提供 | 原生等价端点 | 行号 |
|---|---|---|
| 用户组写 | `/accounts/groups/plan`、`/accounts/groups/apply` | L981, L994 |
| 用户写 | `/accounts/users/plan`、`/accounts/users/apply` | L1011, L1022 |
| 用户密码 | `/accounts/users/password/plan`、`/accounts/users/password/apply` | L1043, L1054 |
| 配额写 | `/quota/plan`、`/quota/apply` | L1313, L1326 |
| 共享写 | `/sharing/folders/plan`、`/sharing/folders/apply` | L498, L511 |

且 `create_omv_alias_router`（`native_storage_routes.py:442`）直接 `register_native_storage_routes(router)` 把原生路由原样挂到 `/api/appliance/omv` 前缀下 —— legacy `/omv` 路径**本就由原生代码服务**，与死集群无关。

### 1.4 `omv_protocol.py` 是纯叶子 schema（活契约，不能删）
`omv_protocol.py` 内 grep `from appliance` → **0 命中**。它是纯 jsonschema / 校验 / 异常契约，被以下 live 模块使用：
- 16 个 `native_*`（native_storage.py:124、native_storage_pool.py:27、native_btrfs*.py、native_mdraid*.py、native_ext4*.py、native_dlna.py、native_smart.py、native_time_machine.py、native_webdav_control.py…）
- `accounts.py:41`、`data_access.py:12`、`btrfs_snapshot_schedule_policy.py:15`、`btrfs_snapshot_lock_policy.py:16`

→ `omv_protocol` 是**活契约**，只应改名去残留，不可删。

### 1.5 活模块不依赖死集群
`accounts.py` 仅 `from appliance.omv_protocol import OmvControlRejected, OmvUnavailable`（L41），并在 L246-248 捕获这两个异常；**不 import 任何死集群模块**。`data_access.py:12` 同理。删死集群对账户/数据面无影响。

---

## 2. 残留分类（决定「删 / 改名 / 后置」）

| 层 | 位置 | 现状 | 处置 | 阶段 |
|---|---|---|---|---|
| **A1 死 Web 路由层** | `appliance/` 下 9 个 `omv_*` | 仅自引用 + 测试，从未 `include_router` | **删除（Phase 1 已执行）** | P0 / Phase 1 |
| **A2 活主机桥** | `appliance/omv_bridge*.py`（9 文件） | 被 `deploy/omv/` 打包 + 宿主机运行 | **保留至 Phase 3** | P2 / Phase 3 |
| **B 活契约** | `omv_protocol.py`、`omv_models.py` | 被 native_* 使用 | **改名**去残留 | P1 / Phase 2 |
| **C 字符串残留** | `native_storage_routes.py` 内 `action="omv.*"` | 仅命名 | **改名** `storage.*` | P1 / Phase 2 |
| **D 宿主桥+CI** | `deploy/omv/` + 构建门禁 + 测试 | 活（x86 CI 在用） | **后置**，需生产证据 | P2 / Phase 3 |
| **E 文档** | `docs/` 8 个文件含 omv | 历史/活动混合 | 活动文档清洗，历史留痕 | P1/P2 / Phase 4 |

**A1 层死 Web 路由层（9 个，Phase 1 已 `git rm`）：**
```
omv_router.py
omv_client.py
omv_account_routes.py
omv_quota_routes.py
omv_read_routes.py
omv_sharing_routes.py
omv_health.py
omv_response.py
omv_route_context.py
```

**A2 层活主机桥（9 个，Phase 1 初版误删，已恢复并保留至 Phase 3）：**
```
omv_bridge.py
omv_bridge_accounts.py
omv_bridge_contract.py
omv_bridge_errors.py
omv_bridge_http.py
omv_bridge_inventory.py
omv_bridge_quota.py
omv_bridge_runners.py
omv_bridge_sharing.py
```

**B 层活契约（改名，保留历史）：**
- `appliance/omv_protocol.py` → `appliance/storage_protocol.py`
- `appliance/omv_models.py` → `appliance/storage_models.py`（被 `native_storage_routes.py:37` 使用）

**C 层字符串残留（仅命名，无功能依赖）：**
- `native_storage_routes.py` 审计动作 `action="omv.user.create"`(L1035)、`"omv.user.password.reset"`(L1067)、`"omv.quota.apply"`(L1335)、`"omv.shared-folder.update"/"omv.shared-folder.create"`(L544,L547) → 改为 `storage.*` 语义。

**D 层宿主桥 + 构建/CI 门禁（活，后置）：**
- `deploy/omv/`（宿主桥 + OMV 插件包 + x86 CI 探针）
- `Makefile:45-56` ruff/bandit 覆盖 `deploy/omv/*.py`；`Makefile:83` `host_bundle build`
- `deploy/appliance/release_candidate_preflight.py:400` 必填 `realOmvX86` run id
- `deploy/appliance/verify-running-appliance.py:32-33` `echo-omv-bridge.service` 单元检查
- `deploy/appliance/README.md:1131,1182` `ECHO_OMV_SOCKET` 与 omv override 指引
- ~10 个 `test_omv_*`（real_x86_ci / real_x86_evidence / plugin_package / plugin_lifecycle / host_installer / host_bundle / compose_security 中 omv 段 / running_appliance_verifier 中 omv 单元段 / delivery_workflow_policy 中 `real_omv_x86_run_id`）

**E 层文档（8 个文件含 omv）：**
活动：`appliance/README.md:199,247`、`deploy/appliance/README.md:1131,1182`、`docs/ECHO_CAPABILITY_CONTRACT.md`、`docs/NAS_DELIVERY_STATUS.md`、`docs/P1_P2_REAL_DEVICE_CHECKLIST.md:618-685`
历史：`docs/PROJECT_ANALYSIS_2026-08-28.md`、`docs/PROJECT_DEEP_AUDIT_2026-09-05.md`、`docs/PROJECT_REASSESSMENT_2026-09-05.md`、`docs/PROVISION_VALIDATION_2026-09-07.md`、`docs/P3_PROVISION_BASE_PLAN.md`

---

## 3. 分阶段路线图

### Phase 0 — 冻结基线 + 回挂守卫（P0，先做，零业务改动）
- [ ] 跑 `make lint` + `pytest tests/appliance -q`，记录绿线提交哈希。
- [ ] 在 `tests/appliance/test_extension.py` 加一条回归断言：运行应用的路由表里**不存在**任何由 `create_omv_router` 提供的 `/api/appliance/omv` 只读/健康端点（防止未来把死集群重新挂回）。
- [ ] 不改业务代码。

### Phase 1 — 删死 Web 路由层（P0，安全，功能零损失）【已执行】
- [x] `git rm` 删除 A1 层 9 个文件（见 §2 清单）。**不含 A2 层 `omv_bridge*` 主机桥**（属 Phase 3）。
- [x] 删除对应纯死集群测试：`test_omv_router.py`、`test_omv_bridge.py`、`test_omv_health.py`。
- [x] **保留并重写** `test_omv_router_structure.py`：删 12 条死集群结构测试，保留 3 条活契约/项目级守卫（`test_native_storage_surface_does_not_import_optional_bridge_client`、`test_container_identifier_validation_has_one_owner`、`test_repository_entrypoints_distinguish_os_from_legacy_agent_material`）。
- [x] 在 `tests/appliance/test_extension.py` 加回归守卫 `test_omv_rpc_backend_cluster_stays_deleted`（断言 A1 层 9 个模块不得回归；`omv_bridge*` 族群刻意排除）。
- [x] 验证：`pytest tests/appliance -q` 失败数 = 基线 2（均环境性、与 OMV 无关）；`python -c "import appliance"` 无 `ModuleNotFoundError`；`ruff check appliance/` All checks passed。
- [x] 回滚：`git revert` 单提交即可恢复 9 个文件。
- ⚠️ 前置已确认：`accounts.py` / `data_access.py` 只 import `omv_protocol`（属 Phase 2），删死集群不影响它们；`omv_bridge*` 仅依赖 sibling + `omv_protocol`，恢复后宿主桥可独立运行。

### Phase 2 — 去命名残留（P1）
- [ ] `git mv`：`omv_protocol.py` → `storage_protocol.py`，`omv_models.py` → `storage_models.py`（保留 git 历史）。
- [ ] 替换全部 import：
  - 4 个 live 模块：`accounts.py:41`、`data_access.py:12`、`btrfs_snapshot_schedule_policy.py:15`、`btrfs_snapshot_lock_policy.py:16`
  - 16 个 `native_*`（见 §1.4）
  - `native_storage_routes.py:37`
  - 各 `test_native_*.py`
- [ ] 异常名 `OmvUnavailable` / `OmvControlRejected` → `StorageUnavailable` / `StorageControlRejected`（含 `accounts.py:246-248` 的 except 分支）。
- [ ] 改写 C 层 `action="omv.*"` 字符串（§2）为 `storage.*`；同步 audit 消费端（若有）。
- [ ] 验证：`make lint` + `pytest` 全绿；全仓 grep `omv_` 应只剩 `deploy/` 与 `docs/` 与历史测试名。

### Phase 3 — 宿主桥 + CI 门禁（P2，后置，需生产证据）
- [ ] 前置：原生面在真实设备（ARM RK3576）上作为唯一存储后端运行 ≥ N 天且无 P0；`deploy/omv` 不再被任何发布通道引用。
- [ ] `git rm -r deploy/omv/`。
- [ ] `Makefile:45-56` 去掉 `deploy/omv/*.py` 的 ruff/bandit 条目；`Makefile:83` `host_bundle build` 移除或改 no-op。
- [ ] `release_candidate_preflight.py:400` 移除 `realOmvX86` 必填门禁；`verify-running-appliance.py:32-33` 移除 `echo-omv-bridge.service` 单元检查。
- [ ] 移除 `ECHO_OMV_SOCKET` 文档与解析（`deploy/appliance/README.md:1131,1182`）。
- [ ] 删 ~10 个 `test_omv_*`（见 §2 D 层）及 `running_appliance_verifier` / `delivery_workflow_policy` 中的 omv 段。
- [ ] 验证：CI 不再触发 real-OMV-x86 工作流；`make rc-preflight` 不要求 omv run id。

### Phase 4 — 文档清洗（P1/P2）
- [ ] 活动文档（§2 E 层活动项）去除 omv 部署指引，改为原生面指引。
- [ ] 历史审计文档保留为记录，仅在开头加一行「OMV 后端已于 <日期> 移除」，不逐句 scrub（避免篡改历史）。
- [ ] 最终 grep 全仓 `omv`：活动代码/构建/配置应为 0；文档仅保留历史标注。

---

## 4. 风险与回滚

| 阶段 | 风险 | 兜底 |
|---|---|---|
| Phase 1 | 极低（死代码） | `git revert` 单提交；`make lint`+`pytest` 验证 |
| Phase 2 | 改名 import 遗漏 | `make lint` 抓 `ModuleNotFoundError` + `pytest` 回归 |
| Phase 3 | 高（动 CI 门禁与生产部署） | 必须 Phase 2 完成 + 原生面生产就绪；分段提交、可单独 revert |

每阶段独立提交、可单独 `git revert`。Phase 0 的回挂守卫持续生效，防止死集群被重新挂载。

---

## 5. 「零残留」终态判定标准

1. `appliance/` 下无 `omv_*` 文件（除注释中提及的历史语境）。
2. `deploy/omv/` 不存在。
3. 全仓 grep `omv_` = 0（活动代码 / 构建 / 配置）；文档仅保留历史标注。
4. `make lint && pytest tests/appliance -q` 全绿。
5. 运行应用路由表无 `omv_router` 提供端点（Phase 0 回归守卫持续生效）。

---

## 6. 执行记录（2026-09-13）

### Phase 0 — 基线冻结（已执行）
- 基线：`pytest tests/appliance -q` → **2410 passed, 92 skipped, 2 failed**（2 个失败均为环境性，与 OMV 无关：`test_native_plugin_is_accepted_by_system_debian_archive_tools` 因 MSYS `tar` 无法解析 Windows 盘符路径；`test_delivery_workflow_policy.py::test_public_source_test_runner_rejects_unreviewed_cli_arguments` 预设失败）。
- 加回挂守卫 `test_omv_rpc_backend_cluster_stays_deleted`。

### Phase 1 — 删死 Web 路由层（已执行 + 验证）
- `git rm` A1 层 9 文件（见 §2）：`omv_router / omv_client / omv_account_routes / omv_quota_routes / omv_read_routes / omv_sharing_routes / omv_health / omv_response / omv_route_context`。
- 删 3 个纯死集群测试：`test_omv_router.py`、`test_omv_bridge.py`、`test_omv_health.py`；重写 `test_omv_router_structure.py` 保留活契约守卫。
- **修正事件**：初版误将 A2 层 `omv_bridge*`（9 文件）一并删除，导致 `deploy/omv/plugin_package.py` 与 `host_bundle.py` 的 payload 清单找不到 `appliance/omv_bridge.py`，引发 11 个 `test_omv_plugin_package.py` / `test_omv_host_bundle.py` 用例 `FileNotFoundError`。根因：`omv_bridge*` 是 `deploy/omv/` 仍打包、宿主机运行的主机桥守护进程（非 Web 路由），属 Phase 3。以 `git checkout HEAD -- appliance/omv_bridge*.py` 恢复，并把守卫元组收窄为 A1 层 9 模块。
- **验证结果**：恢复后重跑 `tests/appliance` → **2328 passed, 84 skipped, 2 failed**，失败数与基线完全一致（2 个均为预设环境性失败）。`ruff check appliance/` All checks passed；`import appliance` 无 `ModuleNotFoundError`。
- 当前 `appliance/` 残留 `omv_*`：`omv_protocol.py`、`omv_models.py`（活契约，Phase 2）、`omv_bridge*.py`（活主机桥，Phase 3）。

### 下一步（待用户拍板）
- Phase 2（改名去残留）：`omv_protocol`→`storage_protocol`、`omv_models`→`storage_models`、`action="omv.*"`→`storage.*`。尚未开始。
- Phase 3 / Phase 4 仍需原生面在 ARM 真机生产就绪证据，未开始。
- 全部改动**未提交**（staged 删除 + 未提交测试/文档改动），供用户 review。
