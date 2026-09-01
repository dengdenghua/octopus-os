"""HTTP request contracts and static fallback pages for paper trading."""

from __future__ import annotations

import html

from pydantic import BaseModel

from .upstream_url import upstream_origin


class _OrderIn(BaseModel):
    code: str
    side: str = "buy"
    order_type: str = "market"
    price: float | None = None
    qty: int = 100


class _CredentialsIn(BaseModel):
    phone: str = ""
    password: str = ""


class _GroupIn(BaseModel):
    name: str = ""


class _StockIn(BaseModel):
    code: str = ""


class _FavIn(BaseModel):
    code: str = ""


class _PlatformApplyIn(BaseModel):
    """申请/扩大配资合约(真实操作,需 confirm)。"""

    contract_type: int = 1  # 1按天 2按周 3按月
    principal: float = 1000.0  # 保证金
    multiple: int = 10  # 倍数
    confirm: bool = False


class _PlatformOrderIn(BaseModel):
    """平台真实买卖委托(需 confirm)。entrust_type: 0限价 1市价。"""

    contract_id: str = ""
    stock_code: str = ""
    stock_name: str = ""
    entrust_type: int = 0
    price: float | None = None
    qty: int = 100
    confirm: bool = False


class _PlatformMoneyIn(BaseModel):
    """追加资金 / 提盈(需 confirm)。"""

    contract_id: str = ""
    money: float = 0.0
    confirm: bool = False


class _PlatformCancelIn(BaseModel):
    """撤单(需 confirm)。"""

    order_id: str = ""
    contract_id: str = ""
    confirm: bool = False


class _PlatformStockIn(BaseModel):
    """平台卖出面板查询(只读)。"""

    contract_id: str = ""
    stock_code: str = ""


class _CheckInRequest(BaseModel):
    """手动触发平台每日签到；页面必须明确传入 confirm。"""

    confirm: bool = False


class _CheckInScheduleIn(BaseModel):
    """自动签到开关与上海时区执行时刻。"""

    enabled: bool = False
    hour: int = 8
    minute: int = 5


class _PlatformSessionIn(BaseModel):
    """Platform JWT copied from the same-origin embedded login page."""

    token: str = ""


def _proxy_disabled_page(base_url: str) -> str:
    """Explain why the optional same-origin upstream proxy is unavailable."""
    origin = upstream_origin(base_url) or "http://114.66.32.152:58868"
    safe_origin = html.escape(origin, quote=True)
    return f"""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<title>模拟炒股 · 平台原站未接入</title>
<style>
  body{{margin:0;background:#10131f;color:#e6e9f0;
       font-family:-apple-system,"PingFang SC","Microsoft YaHei",sans-serif;
       display:flex;align-items:center;justify-content:center;height:100vh;}}
  .box{{max-width:560px;padding:28px 32px;background:#1a1d33;
        border:1px solid #39415f;border-radius:8px;line-height:1.9;}}
  h1{{font-size:16px;margin:0 0 12px;color:#f0b90b;}}
  code{{background:#10131f;border:1px solid #39415f;border-radius:3px;
        padding:1px 6px;font-size:12px;}}
  p{{margin:10px 0;font-size:13px;color:#9aa4b8;}}
  a{{color:#f0b90b;}}
</style></head><body><div class="box">
<h1>平台原站接入未开启</h1>
<p>本页通过同源反向代理嵌入平台原站，在可信的单用户本地实例中默认开启。
   若看到此页，请确认 <code>proxy_origin</code> 与
   <code>allow_same_origin_third_party_scripts</code> 未被显式关闭，且上游是格式有效的
   HTTP 或 HTTPS 地址。</p>
<p><b>开启前请了解代价</b>:同源代理会让原站脚本以本应用的 origin 权限运行,
   可能访问父页面会话并以当前用户身份调用 API；HTTP 上游的传输也未加密。
   生产或多用户部署不得开启。</p>
<p>也可以直接在新窗口打开原站(不共享登录态):
   <a href="{safe_origin}/trade/#/transaction" target="_blank" rel="noreferrer noopener">
   {safe_origin}/trade ↗</a></p>
</div></body></html>"""


def _authenticated_host_page() -> str:
    """Return the inert notice used when host authentication is enabled."""
    return """<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>模拟炒股不可用</title><style>
body{font-family:system-ui,sans-serif;margin:0;background:#0b1020;color:#e8edf8}
.card{max-width:680px;margin:12vh auto;padding:28px;border:1px solid #29334d;
border-radius:16px;background:#121a2e;line-height:1.7}h1{font-size:22px;margin-top:0}
</style></head><body><main class="card"><h1>模拟炒股在当前部署中不可用</h1>
<p>当前实例已开启身份认证。平台账户和本地交易状态尚未实现逐用户隔离，
因此相关行情、交易、同源代理和实时连接均已安全关闭。</p>
<p>请仅在可信的单用户本地实例中启用此插件。</p></main></body></html>"""
