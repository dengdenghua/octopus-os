# tools/lint · 不变量与仓库质量门禁

`invariant_check.py` 负责语义不变量；同目录的独立 ratchet 还覆盖导入方向、
异步阻塞、异常吞噬、特性开关、孤儿模块、路径与文档漂移。CI 以这些脚本的
实际退出码为准，本页不再维护容易过期的规则数量或代码行数声明。

---

## 实现清单（`ALL_RULES` — 实际运行的规则）

| Rule | 守护不变量 | AST/Regex |
|---|---|---|
| LINT-02 | "数字是诗意不是契约" · 禁止硬编码 8（arms/cerebrum 作用域，severity=warning） | Regex |
| LINT-03 | NAMING.md · 生物名不入代码（全局） | AST |
| LINT-04 | EVO-I1 + CC-3 · 禁止直接 LLM SDK（全局） | AST (Import) |
| LINT-05 | BDG-I2 · Task 必带预算（导入 platform.models.Task 的文件） | AST (Call kwargs) |
| LINT-09 | REF-I5 · 反射层禁止 LLM 生成（reflex 作用域） | AST |

**已退休的 2 条**（目标子系统在 biological→neutral 改名中删除；LINT-03 又永久禁止这些名字重现，故不可能再触发，已从 `ALL_RULES` 移除，不作为空守护充数）：
- LINT-01 NO_BYPASS_IMMUNITY（打 `beak.bite()`/`immunity.check()` — 包已删）
- LINT-10 CRDT_NOT_LWW（要求 `dna`+`genome` 包 — 均已删）

**从未实现的 3 条**（都需要更复杂的跨文件分析，Core 阶段再加，不在 MVP 硬守范围）：
- LINT-06 PERSONAL_NO_EGRESS（需全程序调用图）
- LINT-07 NO_GENESTUDIO_SHORTCUT（需数据流分析）
- LINT-08 MUTATION_SINGLE_FIELD（runtime only）

---

## 使用

```bash
# 扫整个项目
python -m tools.lint.invariant_check runtime/

# 只看一条规则
python -m tools.lint.invariant_check --rule LINT-03 runtime/

# JSON 输出（CI 消费）
python -m tools.lint.invariant_check --json runtime/

# pre-commit hook 示例（.pre-commit-config.yaml）
- repo: local
  hooks:
    - id: invariant-lint
      name: echo-agent invariant lint
      entry: python -m tools.lint.invariant_check
      language: system
      types: [python]
      pass_filenames: true
```

---

## 退出码

| 码 | 含义 |
|---|---|
| 0 | 无违规 |
| 1 | 有 error 级违规（阻断 CI）|
| 2 | 工具自身异常 |

warning 级不阻断 CI。

---

## 扩展

新增一条规则：

```python
class MyNewRule(Rule):
    rule_id = "LINT-XX"
    severity = "error"

    def check(self, ctx: LintContext) -> Iterable[LintIssue]:
        for node in ast.walk(ctx.tree):
            if some_bad_pattern(node):
                yield self.issue(ctx, node, "problem description")

# 注册到 ALL_RULES
ALL_RULES.append(MyNewRule())
```

---

## 架构原则

本文件刻意遵循几条：
- **零第三方依赖**（纯 stdlib：`ast` / `re` / `argparse` / `pathlib`）
- **< 400 行**：单文件足够看完，无隐藏复杂度
- **每条规则独立类**：新增/修改互不影响
- **误报可白名单**：`ctx.in_package()` 判断路径，关键模块可豁免

---

## 待实现（Core 阶段）

- [ ] LINT-06 PERSONAL_NO_EGRESS（需要 `@privacy(personal)` 装饰器追踪）
- [ ] LINT-07 NO_GENESTUDIO_SHORTCUT（需要调用图分析）
- [ ] pyright plugin 版本（更强类型信息）
- [ ] fix 模式（--fix 自动修正部分违规）
- [x] 与 ruff / mypy 联合 pipeline（见 `.github/workflows/ci.yml`）
