# P1 · OS 后端去 fork 运行手册

> 目标(见 [OS_DIFFERENTIATION.md](OS_DIFFERENTIATION.md) §3):os 不再 fork agent 的
> `runtime/`(~22 万行),而把 agent 当 **pinned 依赖**装入,`appliance/` 经 P0 扩展
> API 挂载。os 后端 = 纯 appliance 层 + 一个 agent 版本钉,差距从此复利、不再漂移。

## 1. 前提(已具备)

- **agent 已可打包**:`octopus-agent/pyproject.toml` 声明 `name=octopus-agent`、
  `version=0.2.0`、`[tool.setuptools.packages.find] include=["runtime*","tools*"]`
  → `pip install` 即暴露 `runtime` 包,import 路径不变。
- **P0 扩展 API 已落地并发布**:母体 `fedd0c5`(`runtime/platform/extensions.py`
  + create_app/register_all 钩子);os `24e9bb1`(appliance 改经扩展点)。

## 2. 已完成的去风险证明

**① appliance 是 agent 的干净消费者**:`appliance/` 对 `runtime.*` 的全部依赖只有
5 个模块,且都在母体**已提交**树中(非 WIP):

```
runtime.adapters.integrations.local_auth        (+ .config)
runtime.execution.suckers.registry              (+ .testing)
runtime.safety.auth.identity
```

**② 黄金启动证明(非破坏性)**:用纯母体 agent(无任何 appliance 代码)+ 仅把
os 的 `appliance/` 经 `OCTOPUS_APP_EXTENSIONS` 挂上,**PYTHONPATH 完全不含 os 的
runtime/**:

```bash
rsync -a --exclude=__pycache__ octopus-os/appliance /tmp/os-consumer/
cd octopus-agent   # = 已发布 agent 的替身
PYTHONPATH=/tmp/os-consumer OCTOPUS_APPLIANCE=1 \
  OCTOPUS_APP_EXTENSIONS=appliance.extension \
  OCTOPUS_SKILL_EXTENSIONS=appliance.pm_skills:register_pm_skills \
  OCTOPUS_NAS_ROOT=/tmp/nas OCTOPUS_ADMIN_PASSWORD=xxx \
  .venv/bin/python -m runtime serve --config config.example.yaml --port 8022
```

结果:appliance 启动器 4 + 本地认证 3 + 文件管理器 6 路由全部经扩展点挂载,
`app extension loaded: appliance.extension:register_app`。**→ os = appliance/ +
agent依赖、零 forked runtime/ 在后端已被证明可行。**

## 3. 阻碍点(切换前必须先解决)

os 工作树的 `runtime/` 里有**未提交 WIP**(fork 时 rsync 带入的母体「就地冻结」
WIP):`runtime/core/cerebrum/react_*` 拆分、`runtime/sensing/gateway/realtime_*`
拆分、`executor.py`/`skill_forge.py` 等(8 改 + ~12 新)。

**直接 `rm -rf runtime/` 会丢掉这些改动。** 切换前须先二选一:
- (a) 确认这批 WIP 已在母体落地并发布到所钉版本 → os 这份冗余副本可弃;或
- (b) 用户显式确认这批 WIP 可丢弃 / 已无用。

在此之前**不执行第 4 节的删除**。

## 4. 切换步骤(阻碍解决后,机械执行)

1. **钉版本**:把 agent 作为依赖(选所钉 sha,当前建议 `fedd0c5`):
   - `deploy/appliance/Dockerfile`:删掉 `COPY runtime/ ...`,改
     `RUN pip install "octopus-agent @ git+https://github.com/dengdenghua/octopus-agent.git@fedd0c5"`
   - 本地开发:`pip install -e ../octopus-agent`(可编辑装母体)。
2. **删继承副本**:`git rm -r runtime/ tools/`(appliance/ 不依赖 os 本地这两份)。
3. **保留**:`appliance/`、`frontend/`、`deploy/`、`tests/appliance/`、config、docs。
4. **验证**(runtime 现在来自包):
   - `python -m runtime serve` + `OCTOPUS_APP_EXTENSIONS=appliance.extension`
     → appliance 路由挂载(同第 2 节证明)。
   - `pytest tests/appliance/`(51 测试;它们 import 的 runtime 模块由包提供)。
5. **回滚**:`git revert`(runtime/ 从历史恢复),或重新 `git checkout` 删除前的 tag。

## 5. 边界

- **本手册只覆盖后端**。前端去 fork 是 **P2**:用 os 自己的窗口管理器把 agent
  工作台当一个应用开在窗口里(agent 作服务跑、其 UI 在 os 桌面窗口加载),
  os 不必 fork agent 前端。见 OS_DIFFERENTIATION.md §3 P2。
- 升级 agent = 改一处版本钉 + 跑第 4 节验证;appliance 层不受影响。
