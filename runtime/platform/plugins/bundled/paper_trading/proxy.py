"""同源反向代理:把平台原站(交易网页 + 其 ``/api``)代理到本机后端。

**为什么需要代理,而不是直接 iframe 原站**

1. 原站启动脚本末尾有一行没有守卫的 ``var shell = window.require('electron').shell``。
   普通浏览器里 ``window.require`` 存在但不可调用,会抛 ``TypeError`` 并中断整个
   启动脚本 —— 页面停在"加载中"。代理在 HTML 上把这行改写成 ``null`` 即可绕过。
2. 原站登录态存在**它自己 origin** 的 ``localStorage``,iframe 用不上我们后端
   已缓存的 JWT,导致每次都要重新登录。同源代理后即可在 ``<head>`` 注入
   ``localStorage.userInfo``,免去重复登录。

**启用边界**

同源代理意味着原站的 JS 以**我们 origin 的权限**运行，可访问父页面会话并以当前
用户身份调用 API。因此认证/多用户宿主仍会禁用它，调用方也保留两个可显式关闭的
布尔开关。当前平台只提供 HTTP，本模块接受经过严格规范化的 HTTP(S) origin；
实时行情和自动交易则继续在 ``live.py`` / 插件生命周期层严格要求 HTTPS。

结构照 ``runtime/sensing/gateway/storage_proxy_router.py``:头白名单(而非盲转)、
路径前缀白名单、请求体上限、``follow_redirects=False``、流式转发且 ``aclose()``
放在 ``finally``、上游不可用返 503 + ``Retry-After``。
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import json
import logging
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any
from urllib.parse import unquote

import httpx
from fastapi import APIRouter, HTTPException, Request, Response, WebSocket
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse

from .upstream_url import upstream_origin

_logger = logging.getLogger(__name__)

_REQUEST_BODY_LIMIT = 16 * 1024 * 1024

# 上游只允许这几个前缀:交易网页、它的 API、静态资源。
# 其余一律 404,避免这条路由退化成任意 URL 转发器。
_ALLOWED_PREFIXES = ("trade", "api", "static", "socket.io")

_FORWARDED_REQUEST_HEADERS = {
    "accept",
    "accept-language",
    "content-type",
    "if-modified-since",
    "if-none-match",
    "range",
    # 平台用非标准的 `token` 头鉴权(见 live.py::_request),
    # 照抄 storage 代理的白名单会把它丢掉。
    "token",
}

# 刻意**不**转发 content-security-policy / x-frame-options:
# 上游若带了这些头,会把我们自己的 iframe 嵌入拦掉。
# 也不转发 content-encoding —— 我们向上游要 identity,由本机中间件负责压缩。
_FORWARDED_RESPONSE_HEADERS = {
    "accept-ranges",
    "cache-control",
    "content-disposition",
    "content-length",
    "content-range",
    "content-type",
    "etag",
    "last-modified",
}


def _safe_upstream_path(path: str) -> str:
    clean = str(path or "").strip().lstrip("/")
    if not clean:
        raise HTTPException(404, "unknown upstream route")
    # Validate the fully decoded meaning, not merely the first form Starlette
    # handed us. Otherwise ``trade/%252e%252e/admin`` can become traversal
    # only after a downstream proxy/upstream performs another decode.
    decoded = clean
    try:
        for _ in range(8):
            expanded = unquote(decoded, errors="strict")
            if expanded == decoded:
                break
            decoded = expanded
        else:
            raise ValueError("excessive nested path encoding")
    except (UnicodeDecodeError, ValueError) as exc:
        raise HTTPException(404, "unknown upstream route") from exc
    if "%" in decoded or "\\" in decoded or any(ord(char) < 32 for char in decoded):
        raise HTTPException(404, "unknown upstream route")
    # 末尾斜杠要放过 —— ``trade/`` 正是原站入口的常态形式;
    # 但中间的空段(``a//b``)和 ``.`` / ``..`` 一律拒绝。
    parts = decoded.rstrip("/").split("/")
    if any(part in {"", ".", ".."} for part in parts):
        raise HTTPException(404, "unknown upstream route")
    if parts[0] not in _ALLOWED_PREFIXES:
        raise HTTPException(404, "unknown upstream route")
    return clean


# ── 登录态注入 ───────────────────────────────────────────


def _b64url_decode(seg: str) -> bytes:
    return base64.urlsafe_b64decode(seg + "=" * ((4 - len(seg) % 4) % 4))


def _session_bootstrap(state_dir: Path, credentials_file: Path) -> str:
    """构造把已缓存 JWT 写进 ``localStorage.userInfo`` 的 ``<script>``。

    只读本地文件、自己解 JWT —— 刻意不调用 ``PlatformClient`` 上那些方法,
    因为它们基于阻塞 ``urllib`` 且可能触发一次真实登录请求,不适合放在 async 路径上。

    无 token 时返回空串:让用户在页面内正常登录,而不是写一个半空的对象进去。
    """
    try:
        token = json.loads((state_dir / "token.json").read_text(encoding="utf-8"))["token"]
    except Exception:  # noqa: BLE001 - 无 token/文件损坏都退回页面内登录
        return ""
    if not token:
        return ""

    claims: dict[str, Any] = {}
    try:
        claims = json.loads(_b64url_decode(token.split(".")[1]))
    except Exception:  # noqa: BLE001 - 解不出就只带 token,页面会自己补
        _logger.debug("paper_trading proxy: JWT claims 解析失败,仅注入 token")

    phone = ""
    with contextlib.suppress(Exception):  # 凭证文件可选
        phone = str(json.loads(credentials_file.read_text(encoding="utf-8")).get("phone") or "")

    user_info = {
        "token": token,
        "memberId": str(claims.get("memberId") or ""),
        "account": str(claims.get("account") or claims.get("sub") or phone),
        "phone": str(claims.get("phone") or phone),
    }
    # 双层 json.dumps:外层把整个 JSON 串安全嵌进 JS 字面量。
    # 上游读法是 JSON.parse(localStorage.getItem("userInfo")).memberId,
    # 所以存进去的必须是**字符串**而不是对象。
    payload = json.dumps(json.dumps(user_info, ensure_ascii=False))
    # **token 是按平台绑定的**。上游每个请求都带一个 ``platform`` 头
    # (取自 ``window.platform``,原站页面里写死是 ``PC``)。我们后端登录时没带
    # 这个头,拿到的是 h5 平台的 token;页面以 PC 平台拿它去请求,上游会判定
    # 「您的登录信息已过期」(20040)—— 实测:同一 token 只带 token 头成功,
    # 加 ``platform: PC`` 立刻 20040,而 ``platform: h5`` 正常。
    # platform 的对齐不在这里做 —— 页面 <body> 里的内联脚本会执行
    # ``window.platform = 'PC'`` 覆盖掉 <head> 的注入,所以改的是那处赋值本身,
    # 见 :func:`rewrite_html`。
    # Never overwrite a fresher session created by the embedded login page.
    # The parent page synchronises that browser token back to the private
    # server-side token file after the upstream accepts it.
    return (
        "<script>try{if(!localStorage.getItem('userInfo')){"
        f"localStorage.setItem('userInfo',{payload});"
        "}}catch(e){}</script>"
    )


# 原站 bundle 里计算 API 基址的唯一一处表达式。浏览器模式下它返回**绝对路径**
# ``/api``,而我们把站点挂在 ``/api/plugins/paper-trading/origin/`` 之下,
# 于是页面内所有接口调用都会打到本机后端的 /api/* 上并 404。
# 这里把这个分支的返回值替换成代理前缀下的 API 基址。
# 注:该表达式在整个 bundle 中只出现一次(已实测确认),替换是精确的。
_API_BASE_PREFIX = "/api/plugins/paper-trading/origin"

# 原站有**两处**独立的 API 基址计算,浏览器模式下都产出绝对路径 ``/api``,
# 会绕过代理直接打到本机后端并 404。两处都要改(实测缺一不可):
#   1. ``P()``:``getS3Configs`` 等少数调用走它;
#   2. ``global.getUrl("/api")``:**绝大多数业务接口**走它
#      (合约/行情/持仓等),漏了它页面会一直"暂无合约 + 网络线路不佳"。
_API_BASE_NEEDLES = (
    ('"browser"===window.PLATFORM?"/api"', f'"browser"===window.PLATFORM?"{_API_BASE_PREFIX}/api"'),
    ('.getUrl("/api")', f'.getUrl("{_API_BASE_PREFIX}/api")'),
    # 第三条通道:socket.io。**持仓列表和合约详情是 WS 推送的,不是 HTTP** ——
    # 漏了这条,页面会显示「您当前无任何持仓」+ 合约卡金额全空 + 距离线 NaN,
    # 控制台反复刷 ws://<本机>/socket.io/ failed。
    # 浏览器模式下原站传的是 window.location.host(即我们的 host),socket.io 默认
    # 连 ``/socket.io/``;这里换成同源 URL + 指向代理前缀的 path 选项。
    # 改的是 vendors chunk 里真正建连的那处 ``io()`` 调用(加 ``path`` 选项),
    # 而不是 app.js 里的调用点 —— ``getSocketIoUrl(e)`` 只接一个参数,
    # 在调用点塞第二个参数会被直接忽略。
    (
        't.socketIo=o()(t.getSocketIoUrlPath,{forceNew:!1,transports:["websocket"]})',
        't.socketIo=o()(t.getSocketIoUrlPath,{forceNew:!1,transports:["websocket"],'
        f'path:"{_API_BASE_PREFIX}/socket.io"}})',
    ),
)


def rewrite_js(text: str) -> str:
    """改写上游 JS:把硬编码的绝对 API 基址 ``/api`` 指到代理前缀下。

    只做这两处精确替换,不做通用 URL 重写 —— 通用重写很容易误伤数据里的
    字符串(比如行情备注),得不偿失。
    """
    for needle, replacement in _API_BASE_NEEDLES:
        text = text.replace(needle, replacement)
    return text


def rewrite_html(html: str, bootstrap: str) -> str:
    """改写上游 HTML:中和 electron 崩溃 + 对齐 platform + 注入登录态。

    只在 ``text/html`` 上做,JS/CSS 走 :func:`rewrite_js`。
    """
    # **token 按平台绑定**:上游每个请求都带 ``platform`` 头(取自
    # ``window.platform``,原站内联脚本写死 ``'PC'``)。我们后端登录时不带该头,
    # 拿到的是 h5 平台的 token;页面以 PC 身份用它请求会被判
    # 「您的登录信息已过期」(20040),表现为自选/合约明细一直空。
    # 实测:同一 token,``platform: h5`` 正常,``platform: PC`` 必 20040。
    # 必须改这处赋值 —— 它在 <body> 里执行,会覆盖 <head> 注入的任何值。
    for quote in ("'", '"'):
        html = html.replace(
            f"window.platform = {quote}PC{quote}", f"window.platform = {quote}h5{quote}"
        ).replace(f"window.platform={quote}PC{quote}", f"window.platform={quote}h5{quote}")

    # 原站这两行在浏览器里必崩:window.require 存在但不是函数。
    # 源码里引号风格不固定,单双引号都处理。
    for quote in ("'", '"'):
        html = html.replace(
            f"var shell = window.require({quote}electron{quote}).shell",
            "var shell = null",
        ).replace(
            f"global.ipcRenderer = window.require({quote}electron{quote}).ipcRenderer",
            "global.ipcRenderer = null",
        )
    if bootstrap and "<head>" in html:
        html = html.replace("<head>", "<head>" + bootstrap, 1)
    return html


# ── 路由 ─────────────────────────────────────────────────


def _upstream_headers(request: Request, origin: str) -> dict[str, str]:
    headers = {
        name: value
        for name, value in request.headers.items()
        if name.lower() in _FORWARDED_REQUEST_HEADERS
    }
    # 向上游要未压缩内容:HTML 需要改写,压缩过就没法做字符串替换。
    headers["Accept-Encoding"] = "identity"
    headers["Referer"] = origin + "/"
    return headers


def _response_headers(response: httpx.Response) -> dict[str, str]:
    return {
        name: value
        for name, value in response.headers.items()
        if name.lower() in _FORWARDED_RESPONSE_HEADERS
    }


def register_origin_proxy(
    router: APIRouter,
    *,
    base_url: str,
    state_dir: str = "~/.echo/data/paper_trading",
    credentials_file: str = "~/.echo/data/paper_trading/credentials.json",
    http_client: httpx.AsyncClient | None = None,
) -> bool:
    """把 ``/origin/{upstream_path:path}`` 挂到给定 router 上。

    路径段刻意用 ``origin`` 而**不是** ``assets``:``_app_auth.py`` 里的
    ``_is_public_plugin_asset_request()`` 会无条件放行所有
    ``/api/plugins/*/assets/*`` 的 GET/HEAD,取名 ``assets`` 会造出一个
    永久免鉴权的开放代理。

    返回是否挂载成功。``base_url`` 必须能严格规范化为 HTTP(S) origin；用户信息、
    非法端口、query/fragment 和畸形主机名都会在底层被拒绝。
    """
    origin = upstream_origin(base_url)
    if not origin:
        _logger.warning("paper_trading proxy: 上游 URL 不是有效 HTTP(S) 地址,代理未挂载")
        return False

    state_path = Path(state_dir).expanduser()
    creds_path = Path(credentials_file).expanduser()

    async def proxy_origin(request: Request, upstream_path: str) -> Any:
        safe_path = _safe_upstream_path(upstream_path)

        content_length = request.headers.get("content-length")
        if content_length:
            try:
                if int(content_length) > _REQUEST_BODY_LIMIT:
                    raise HTTPException(413, "request body too large")
            except ValueError as exc:
                raise HTTPException(400, "invalid content-length") from exc
        body = await request.body()
        if len(body) > _REQUEST_BODY_LIMIT:
            raise HTTPException(413, "request body too large")

        owns_client = http_client is None
        client = http_client or httpx.AsyncClient(
            timeout=httpx.Timeout(connect=5.0, read=60.0, write=30.0, pool=5.0),
            follow_redirects=False,
            # 与 WS 侧一致:上游是固定 IP 的自建服务,不该经系统代理。
            # 本机 NO_PROXY 并未覆盖该 IP,不显式关掉就会绕一圈外部代理。
            trust_env=False,
        )
        url = f"{origin}/{safe_path}"
        try:
            upstream_request = client.build_request(
                request.method,
                url,
                params=list(request.query_params.multi_items()),
                headers=_upstream_headers(request, origin),
                content=body,
            )
            upstream = await client.send(upstream_request, stream=True)
        except httpx.HTTPError:
            if owns_client:
                await client.aclose()
            return JSONResponse(
                {"detail": "平台原站暂时不可用"},
                status_code=503,
                headers={"Retry-After": "2"},
            )

        content_type = upstream.headers.get("content-type", "")
        lowered = content_type.lower()
        is_html = "text/html" in lowered
        is_js = "javascript" in lowered

        # HTML / JS 需要字符串替换,必须读完整 body(无法流式)。
        # 其余内容(CSS/图片/JSON/字体)原样流式透传。
        if is_html or is_js:
            try:
                raw = await upstream.aread()
            finally:
                await upstream.aclose()
                if owns_client:
                    await client.aclose()
            text = raw.decode("utf-8", errors="replace")
            if is_html:
                body_out = rewrite_html(
                    rewrite_js(text), _session_bootstrap(state_path, creds_path)
                )
                return HTMLResponse(content=body_out, status_code=upstream.status_code)
            return Response(
                content=rewrite_js(text),
                status_code=upstream.status_code,
                media_type=content_type or "application/javascript",
            )

        async def _stream() -> AsyncIterator[bytes]:
            try:
                async for chunk in upstream.aiter_raw():
                    if chunk:
                        yield chunk
            finally:
                await upstream.aclose()
                if owns_client:
                    await client.aclose()

        return StreamingResponse(
            _stream(),
            status_code=upstream.status_code,
            headers=_response_headers(upstream),
        )

    @router.websocket("/origin/socket.io/{ws_path:path}")
    async def proxy_socket_io(websocket: WebSocket, ws_path: str) -> None:
        """代理 socket.io 长连接。

        原站的**持仓列表和合约详情靠这条 WS 推送**(``event:"subscribe"`` +
        ``url:"/contractPosition"`` / ``"/contractList"``),不是 HTTP 接口。
        不代理它的话页面会显示「您当前无任何持仓」且合约卡金额全空。

        鉴权靠查询串里的 ``sign``(前端逐次生成),这里原样透传、不做解释。
        """
        # websockets 是重依赖(经 uvicorn[standard] 传递引入),按仓库既有做法懒加载。
        try:
            import websockets
        except ImportError:  # pragma: no cover - 缺依赖时优雅拒绝而非 500
            await websocket.close(code=1011)
            return

        query = websocket.url.query
        ws_origin = origin.replace("https://", "wss://").replace("http://", "ws://")
        target = f"{ws_origin}/socket.io/{ws_path}" + (f"?{query}" if query else "")

        # 上游 WS 除了查询串里的 sign,还校验 Origin/Referer 与 token 头 ——
        # 一个都不带会被回 401(实测),连不上就没有持仓和合约详情推送。
        upstream_headers: dict[str, str] = {
            "Origin": origin,
            "Referer": origin + "/",
        }
        # `Authorization` and `Cookie` belong to the Echo host.  Forwarding
        # either would disclose the user's host session to the third party.
        # The upstream platform uses its distinct non-standard `token` header.
        for name in ("token", "user-agent"):
            value = websocket.headers.get(name)
            if value:
                upstream_headers[name] = value

        await websocket.accept()
        try:
            async with websockets.connect(
                target,
                open_timeout=10,
                additional_headers=upstream_headers,
                # 直连,不走系统 HTTP(S)_PROXY / SOCKS。上游是固定 IP 的自建服务,
                # 经代理只会失败:websockets 见到 SOCKS 变量会要求 python-socks
                # (未安装),报 "connecting through a SOCKS proxy requires python-socks"
                # 而握手永远建不起来 —— 表现就是页面持仓/合约详情一直为空。
                proxy=None,
            ) as upstream_ws:

                async def pump_to_upstream() -> None:
                    while True:
                        msg = await websocket.receive_text()
                        await upstream_ws.send(msg)

                async def pump_to_client() -> None:
                    async for msg in upstream_ws:
                        if isinstance(msg, bytes):
                            await websocket.send_bytes(msg)
                        else:
                            await websocket.send_text(msg)

                # 任一方向先结束就收摊,避免残留的 pump 协程泄漏。
                done, pending = await asyncio.wait(
                    [
                        asyncio.create_task(pump_to_upstream()),
                        asyncio.create_task(pump_to_client()),
                    ],
                    return_when=asyncio.FIRST_COMPLETED,
                )
                for task in pending:
                    task.cancel()
        except Exception as exc:  # noqa: BLE001 - 上游断开/握手失败都只需静默收尾
            # 用 warning 而非 debug:握手失败(如缺 python-socks、上游 401)会让
            # 持仓/合约详情静默为空,是排查时第一个该看到的线索。
            _logger.warning(
                "paper_trading proxy: socket.io 转发结束(%s): %s", type(exc).__name__, exc
            )
        finally:
            with contextlib.suppress(Exception):
                await websocket.close()

    # 每个 method 单独一条 add_api_route:多 method 挂同一条路由会产生重复的
    # OpenAPI operation ID,破坏生成的 TS 客户端(见 storage_proxy_router.py 的同款注释)。
    for method in ("GET", "HEAD", "POST", "PUT", "PATCH", "DELETE"):
        router.add_api_route(
            "/origin/{upstream_path:path}",
            proxy_origin,
            methods=[method],
            operation_id=f"proxy_paper_trading_origin_{method.lower()}",
            include_in_schema=False,
        )
    return True
