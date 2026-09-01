<!--
  Keep short. Maintainers read these carefully · 不必长。
  Checklist 钩上 → green PR · 每条都是硬线。
-->

## What

<!-- 一段话 · 做了什么变化 · 为什么 -->

## Why

<!-- 解决了什么问题 / 服务什么目标 -->

## How

<!-- 关键文件 · 决策点 · 拒绝掉的 alternatives -->

## Test

<!-- 新测试数 · 手测场景 · bench 数字(如果有) -->

```bash
# 我复核用的命令
make production-readiness-static
python -m pytest tests/ -q
python -m tools.lint.invariant_check runtime/ tests/
```

---

## Checklist

硬线 · 任何一条没过 maintainer 就 request changes:

- [ ] `python -m pytest tests/` 绿(不跳过 · 3800+ 基线)
- [ ] `python -m tools.lint.invariant_check runtime/ tests/` 0 issue
- [ ] 新逻辑有新测试(unit 或 integration)· 否则给说法
- [ ] 不加新强依赖(新 soft-dep 走 `[project.optional-dependencies]` · 在 PR 说明动机)
- [ ] 违反不变量的改动同步更新 [invariants.md](../docs/invariants.md)
- [ ] 涉及 SSE / thread state metadata / Budget · bench_runner.py 没回退

## Scope 软线(如果适用)

- [ ] Channel adapter 的 `send()` 走了 constitution gate(看 CONTRIBUTING 里的样板)
- [ ] 新 Skill 按 affinity / cost_profile / trusted_source 填齐
- [ ] 改了 `BASE_SKILL_IDS` / `ATOMIC_SKILL_NAMES` → 两处都改
- [ ] 动了 fork 自 echo 的模块 → 更新 [forklist.md](../docs/forklist.md)

## Related

<!-- Closes #N / Refs #N / Depends on #N -->
