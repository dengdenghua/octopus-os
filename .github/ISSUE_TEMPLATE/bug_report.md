---
name: 🐛 Bug report
about: Something runs but does the wrong thing · 贴数字和复现步骤
title: "[bug] "
labels: bug
---

## 发生了什么

<!-- 一句话:你做了 X,期望 Y,实际 Z。 -->

## 复现

```bash
# 最小命令序列 · 我能直接 copy-paste 跑
```

## 环境

运行 `python -m runtime status` 把输出贴这:

```
```

关键版本(`python -c "import runtime, sys; print(sys.version, runtime.__version__ if hasattr(runtime, '__version__') else '?')"`):

## 贴什么有帮助

- [ ] 相关 thread ID(如果是 backend 报错)· 让维护者能在你的 journal 里查
- [ ] 复现的 minimal agent config / prompt
- [ ] `additional_kwargs.echo` metadata(bench_runner 输出就有)
- [ ] `.scores.jsonl` 最后几行(如果跟自演化有关)
- [ ] `benchmarks/results/runs-*.jsonl` 里失败那一行(如果是 bench)

## 怀疑 / 线索

<!-- 你猜是哪个文件 / 哪条路径?不确定也可以写。 -->
