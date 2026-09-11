# 命名约定

> 本文回答一个问题：**代码里还能看到的 `octopus` 到底是残留还是契约？**
> 结论是——**几乎全是契约，不能删**。误删会破坏已落盘的数据，或让老装机机器起不来。
> 契约由 `tests/test_naming_contract.py` 强制，改动前先看那里。

## 一、速查表

| 维度 | 名字 | 性质 |
| --- | --- | --- |
| 产品名 | **Echo OS** | 对外品牌，已统一 |
| Python 发行包 | `echo-os` | 已统一 |
| CLI 命令 | `echo` / `echo-agent` / `echo-storage` | 已统一 |
| 桌面应用 | `Echo OS Desktop`（`com.echo.os.desktop`） | 已统一 |
| 容器镜像 | `ghcr.io/dengdenghua/echo-os` | **有意保留 echo 品牌，禁改** |
| **git 仓库** | **`github.com/dengdenghua/octopus-os`** | **必须用这个** |
| 执行引擎 ID | `octopus` | **持久化契约，禁改** |

## 二、仓库名：为什么是 octopus-os

这是 fork 后的历史结果，不是疏忽。**实测两个 URL：**

| URL | HTTP 状态 |
| --- | --- |
| `https://github.com/dengdenghua/echo-os` | **404（不存在）** |
| `https://github.com/dengdenghua/octopus-os` | 200 |

历史上仓库内曾大量指向不存在的 `echo-os`。2026-09-09 已统一修正（涉及 31 个文件、60 处），
包括 `scripts/install.sh`、`pyproject.toml` 的 `[project.urls]`、前端「关于」页四种语言的
GitHub 链接，以及首页取 star 数的 API 地址。

**GHCR 镜像名保持 `echo-os` 不变**——它在 `appliance-release.yml:106` 里是显式指定的
对外品牌，与仓库名无关，跟着仓库改名反而会破坏已发布的镜像引用。

## 三、`octopus` 的四类不可改契约（P0）

### 1. 执行引擎 ID

`runtime/execution/engines.py:19-22`

```python
class EngineId(StrEnum):
    OCTOPUS = "octopus"
    # Backward-compatible name used by the pre-unified host tests and older
    # callers.  It intentionally resolves to the same durable engine id.
    NATIVE = "octopus"
    CODEX = "codex"
    OPENCODE = "opencode"
```

`NATIVE = "octopus"` **不是笔误**，是故意的向后兼容别名。引擎 ID 会写进已持久化的
任务记录，改成 `echo` 会让老记录读不出来。

### 2. 持久化 schema 命名空间

| 位置 | 常量 |
| --- | --- |
| `runtime/memory/semantics.py:17` | `octopus.memory_origin.v1` |
| `runtime/execution/agents/collaboration_quality.py:15-17` | `octopus.collaboration_quality.v1` 等 |
| `runtime/execution/agents/team_patterns.py:20` | `octopus.team_pattern_decision.v1` |
| `runtime/execution/host_boundary.py:120` | `octopus.execution.v1` |
| `runtime/execution/parallel_agents/_batch_host.py:66` | `octopus.execution.v1` |

这些前缀**已经写进磁盘上的记录**，改了等于让旧数据失联。

### 3. 历史装机账号

`deploy/provision/base/provision-lib.sh:961-969`（原文注释）：

> 历史教训: rebrand 曾把 unit 的 `User=octopus` 一并改成 `echo`,但两代装机介质建的用户
> 不一致(vmtest preseed 建 `echo`; 更早的裸机/老 ISO 建 `octopus`),写死任一名字在另一种
> 装机上就是 **217/USER crash-loop**。以机器上真实存在的账号为准。

```sh
for u in echo octopus admin; do
  if id -u "$u" >/dev/null 2>&1; then echo "$u"; return; fi
done
```

`octopus` 这一项删掉，用老 ISO 装过的机器会直接崩。

### 4. 清理遗留 octopus 单元的逻辑本身

`deploy/provision/base/provision-lib.sh:1083-1087` 主动清理 `octopus-*.service`
与 `/etc/nginx/sites-enabled/octopus`。**这是清理逻辑，不是残留**，删了反而留下残留：

> 8000 端口被 `octopus-appliance` 占用会让 `echo-appliance` 起不来。必须清掉。

## 四、强制方式

`tests/test_naming_contract.py` 用 9 条断言锁住上述契约，覆盖：

- `EngineId.OCTOPUS` / `NATIVE` 的值
- 四类持久化 schema 前缀
- `probe_run_user` 仍探测 `echo octopus admin`
- 清理逻辑仍在
- 仓库 URL 指向真实存在的 `octopus-os`（且不含 404 的 `echo-os`）
- GHCR 镜像仍保留 `echo-os` 品牌

已做负向验证：把 `OCTOPUS = "octopus"` 改成 `"echo"` 后该测试立即失败，恢复后转绿。

> ⚠️ 该测试位于 `tests/` 根目录，而 `pyproject.toml:273` 的 `testpaths` 目前只指向
> `tests/appliance`。**在 testpaths 修好之前，CI 不会执行它**，需要本地手动跑。

## 五、如果将来真要统一成 echo-os

必须同时满足（缺一不可）：

1. GitHub 上创建 `echo-os` 仓库，或把 `octopus-os` 改名（改名自带重定向，旧 ISO 仍可用）
2. 全文替换 `dengdenghua/octopus-os` → `dengdenghua/echo-os`（**仅限 git 仓库 URL，GHCR 除外**）
3. 为引擎 ID 与 schema 前缀写数据迁移，否则旧记录读不出来
4. `provision-lib.sh` 的账号探测**仍要保留** `octopus`，否则老装机机器 crash-loop
5. 更新本文档与 `tests/test_naming_contract.py`
