---
name: crash-triage
description: "读取 Echo OS 崩溃转储（<data>/crashes/*.json）并做根因分析，给出最小修复建议与可直接粘贴的 bug 报告。当服务异常退出、重启后又不复现、日志里只有一行 traceback，或用户提到崩溃/crash/闪退/半夜挂了/进程没了/SystemExit 时使用。"
license: MIT
type: tool
tags: [crash, diagnostics, reliability, appliance, triage]
---

# Crash Triage

Echo 跑在没有终端的设备上。凌晨三点进程死了，日志里躺着一行 traceback，
等有人来看时进程早就重启过了 —— 现场没了。所以崩溃会被写进一个有界、
去重的转储目录，本 skill 负责把那份转储变成"为什么死 + 改哪一行"。

## 核心约束：不要去问一个已经死了的服务

崩溃转储存在**磁盘**上，不是靠 HTTP 拿的：

```
<ECHO_DATA_DIR 或 <项目根>/data>/crashes/<fingerprint>.json
```

服务已经挂掉的时候 `/api/crashes` 是不可达的。所以**默认走脚本直接读目录**，
HTTP 端点只在服务还活着时用（比如想让面板看一眼）。

```bash
# 全部转储，按最近发生排序
python scripts/triage.py

# 指定 data 目录
python scripts/triage.py --data-dir /opt/echo/data

# 只看某一个
python scripts/triage.py --fingerprint 3f9a1c2b8e7d6a45

# 输出机器可读的 JSON，便于 agent 消费
python scripts/triage.py --json
```

脚本独立于 Echo 运行时：即使 `runtime/` 本身 import 不了（崩溃可能就是它
引起的），它也会退化成纯 JSON 打印，不会跟着一起死。

## 转储格式

一个指纹一个文件，重复崩溃只累加 `occurrence_count`。字段：

| 字段 | 含义 |
|------|------|
| `fingerprint` | 异常类型 + 末 4 帧的哈希，同类崩溃归并成一个 |
| `occurrence_count` | 出现次数。**大于 1 说明是循环崩溃，优先看** |
| `first_seen_at` / `last_seen_at` | 首次 / 末次，用来判断是不是持续发生 |
| `source` | `excepthook`（主线程）/ `threading`（工作线程）/ `asyncio`（异步任务）/ `manual` |
| `context` | 安装钩子时带的标签，如 `component=config` |
| `traceback` | 截断到 8000 字符 |
| `environment` | python 版本、平台、pid、内存档位 |

## 分析顺序

1. **先看 `occurrence_count`**。反复出现的崩溃比一次性噪声有价值得多。
2. **看 `source`**。`threading` / `asyncio` 的异常默认只打印到 stderr，
   很容易被完全忽略 —— 这类往往是"服务还活着但功能悄悄坏了"。
3. **读 `diagnosis` 字段**（脚本已给出本地规则判定）。
   `confidence=high` 且 cause 明确的，基本可以直接照着 `suggested_fix` 改。
4. **本地判定给不出答案时**（`cause=unknown`），才由你来做根因分析：
   读完整 traceback，找最内层**属于 Echo 自己代码**的那一帧
   （排除 site-packages），从那里往上找谁没能兜住这个异常。
5. **给出最小修复**，不是重构方案。一行 try/except 能止血就不要改架构。

## 已知的坑（高频根因）

| 现象 | 根因 |
|------|------|
| 关服务时抛 `SystemExit` | `SystemExit`/`KeyboardInterrupt`/`GeneratorExit` 是 **BaseException**，`except Exception` 和 `contextlib.suppress(Exception)` 都拦不住。关停路径要么显式捕获，要么让它走 |
| 删临时文件时进程没了 | 开发机上的 safe-delete 钩子会 `raise SystemExit`。清理逻辑必须自己吞掉异常，否则会盖掉真正的错误 |
| 重启后又好了，再也不复现 | 大概率是关停竞态，看 `source=asyncio` 且时间在 `close()` 之后的记录 |
| 磁盘被写满 | 写循环没有上限。对照 `occurrence_count` 判断是不是崩溃循环自己写满的 |

## 收尾

分析完让用户确认，再清理已处理的转储：

```bash
curl -X DELETE "http://localhost:8000/api/crashes?cross_tenant=true"   # 服务活着
python scripts/triage.py --clear                                       # 服务已挂，直接删文件
```

**不要**在未告知用户前静默清空 —— 转储是唯一的现场证据。

## 输出给用户的格式

```
崩溃 <fingerprint> · <exception_type>
  次数 N · 首次 … · 末次 …
  判定: <cause> (confidence)
  为什么: …
  改哪里: …
```

后面接你的补充分析和改哪个文件的哪一行。用户要提 bug 时，
`python scripts/triage.py --fingerprint <fp> --report` 输出的就是
可以直接粘进 issue 的纯文本报告。
