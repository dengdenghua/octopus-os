# Echo Storage 文本编辑协议 v1

仓库现已提供可独立启动的轻量 Storage 服务：`runtime.storage.service`。它与桌面、工作台共用 `/api/storage` 网关和文件资源标识，补齐原先缺失的外置文本读写与差异接口。此实现是我们定义的协议，不宣称兼容未拿到源码的旧外置服务私有接口。

## 启动

使用项目 Python 环境，安装项目的 `serve` extra（包含 uvicorn）。源码环境可直接运行模块；安装新版 wheel 后也可使用 `echo-storage serve`。

在启动 Storage 和 Echo 后端的环境中配置相同的私有令牌，以及明确授权的现有目录：

```powershell
$env:ECHO_STORAGE_ROOT = 'C:\实际授权的文件目录'
$env:ECHO_STORAGE_TOKEN = '<至少 24 字符的随机私有令牌>'
$env:ECHO_STORAGE_URL = 'http://127.0.0.1:8767'
.venv\Scripts\python.exe -m runtime.storage.service serve --port 8767
```

令牌也可沿用现有 `~/.echo/storage/api_token` 文件；不要把令牌放入 URL、前端配置或版本库。服务默认只监听 `127.0.0.1`，不启动额外窗口，不直接暴露到公网。`--root` 可以覆盖 `ECHO_STORAGE_ROOT`。

如需跟随 Echo 启动，设置 `ECHO_STORAGE_AUTOSTART=1`。已有 `ECHO_STORAGE_CMD`、独立安装的服务优先；找不到旧服务时，源码环境下的 supervisor 会在明确配置 `ECHO_STORAGE_ROOT` 后启动仓库内实现。冻结的桌面可执行文件不能当成 Python 解释器使用，需配置独立 Python 服务命令或安装提供 `echo-storage` 命令的运行环境。

没有配置目录或令牌时服务拒绝启动；不会自动把整个磁盘或另一个本机文件目录变成可写数据源。现有进程需要重新加载后端代码与配置，旧外置服务也必须升级或换用此实现，前端才会显示编辑入口。

## 能力和接口

`GET /v1/manifest` 的 `capabilities` 包含 `text-edit.v1` 才能启用编辑；`role` 仅表示部署形式。旧服务和只读 embedded fallback 不会被误判为支持写入。

| 接口 | 行为 |
| --- | --- |
| `GET /v1/files/{resource_id}/text` | 返回完整 UTF-8 文本、SHA-256 revision、字节数、`complete: true` |
| `POST /v1/files/{resource_id}/diff` | 比较磁盘当前版本与草稿，返回统一格式差异；不写文件 |
| `PUT /v1/files/{resource_id}/text` | 校验预期版本，以同目录临时文件原子替换原文件 |

写入和差异请求体：

```json
{
  "text": "新的完整文本\n",
  "expected_revision": "上次完整读取返回的 64 位 SHA-256"
}
```

完整读取与保存响应：

```json
{
  "resource_id": "storage-file:v1:...",
  "text": "新的完整文本\n",
  "revision": "新内容的 64 位 SHA-256",
  "size": 22,
  "encoding": "utf-8",
  "complete": true
}
```

示例中的摘要和字节数为字段说明，实际值由服务计算。资源 ID 中的数据源必须匹配授权根目录；仅传入文件路径不能写入。协议保留原始 UTF-8 内容（包括 BOM 与换行），不自动解码其他编码。

| 状态码 | 含义 |
| --- | --- |
| 401 / 403 | 令牌、账号或文件权限不满足要求 |
| 404 | 文件不存在或属于另一个数据源 |
| 409 | 文件版本已变化，必须读取最新版本后合并 |
| 413 | 文本超过 1,000,000 字节，或差异两侧合计超过 4,000 行 |
| 415 | 非 UTF-8 文本或包含二进制内容 |
| 422 | 请求体缺少有效预期版本等必填字段 |
| 503 | 服务连接中断；保存是否完成需要重新读取确认 |

## 界面行为

- 桌面与工作台使用同一个文件预览组件和接口。点击编辑时重新获取完整文本，绝不把截断预览当作保存内容。
- 差异由服务生成，插入一行不会把后续每行误标成修改。
- 发生版本冲突或断线时保留草稿，提供“读取最新版本（保留草稿）”，可比较草稿与最新版本后手动合并。不会自动覆盖或盲目重试。
- 保存期间禁止修改草稿；有未保存更改时阻止直接关闭预览，并对浏览器离开页面提供提醒。
- 外置写入失败不会回退到 embedded 文件上传，从而避免把另一目录中的同名文件覆盖。

## 部署边界

这是配置了授权目录的轻量文件服务，提供浏览、文本搜索、内容读取和文本编辑；不包含 OCR、向量索引、图片生成或旧服务的应用管理功能。不支持的接口返回 404，不模拟成功。

独立服务令牌授予配置根目录的访问权。共享部署下，新编辑/差异接口仅允许已注册的 admin/operator 通过网关调用；普通家庭成员的 OMV 共享目录权限尚未投射到此独立服务，因此不对普通成员开放编辑。既有 appliance 文件 ACL 没有被通用上传绕过。

服务使用根目录进程锁拒绝第二个写入服务，同一进程内串行执行版本检查与提交。其他不使用此协议的本地编辑器不参与此锁：服务会在提交前再次校验内容，但不能声称对任意外部进程提供文件系统级 CAS。需要严格并发保障时，应由此服务独占写入授权目录。

保存日志记录资源标识与结果状态码，不记录文本或令牌；它是运行日志，并非独立的防篡改审计账本。

## 验证

后端测试覆盖完整读写、差异、并发冲突、错误数据源、认证、超限和二进制拒绝、进程排他、网关联调及断线不回退。前端测试覆盖完整文本加载、能力协商、版本参数、草稿保留及读取最新版本后比较。

```powershell
.venv\Scripts\python.exe -m pytest tests/test_storage_text_protocol.py tests/test_storage_proxy_router.py tests/test_storage_supervisor.py tests/test_privacy_boundary.py -q
```

在 `frontend` 目录运行 `pnpm check` 和对应 `api.text-edit`、`local-database-edit` 测试。
