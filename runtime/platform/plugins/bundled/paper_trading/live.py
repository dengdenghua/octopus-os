"""平台实时行情只读客户端(live 数据源)。

按对方 App 的接口协议,连接其后端拉取**真实行情数据**:

- 登录: ``POST /api/member/member/login``,密码用 **RSA-1024(PKCS#1 v1.5)** 加密
  (公钥来自 ``POST /api/system/systemConfigs/getPublicKey``,与 App 一致);
- 大盘概览: ``POST /api/market/v2/data/doAction?event=todayStock``,返回 **gzip JSON**
  (真实指数价格 + 全市场涨跌家数 + 市场状态);
- 公司简况: ``GET /api/market/brief?code=<symbol>``(真实财报/基本面)。

默认只拉行情;同时提供**可选的账户/合约/下单方法**(申请资金、买入、卖出等)供插件调用。
JWT token 缓存在本地状态目录(默认 ~/.echo/data/paper_trading/token.json),
过期自动重登。账号密码只在登录时使用,不写入任何文件(从环境变量或凭证文件读取)。
"""

from __future__ import annotations

import base64
import gzip
import json
import logging
import os
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Any

from .upstream_url import secure_upstream_origin

try:
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import padding

    HAS_CRYPTO = True
except Exception:  # pragma: no cover
    HAS_CRYPTO = False

_logger = logging.getLogger(__name__)

ENV_PHONE = "PAPER_TRADING_PHONE"
ENV_PASSWORD = "PAPER_TRADING_PASSWORD"

# 平台返回这些 code 表示会话失效(登录信息已过期),自动重登后重试一次。
_AUTH_EXPIRED_CODES = {20040, 20041, 20042, 10001}


class PlatformClientError(RuntimeError):
    """平台客户端错误(登录失败/网络/解析)。"""


class _NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Reject every redirect so platform credentials never change origin."""

    def redirect_request(
        self,
        req: Any,
        fp: Any,
        code: int,
        msg: str,
        headers: Any,
        newurl: str,
    ) -> None:
        return None


_NO_REDIRECT_OPENER = urllib.request.build_opener(
    urllib.request.ProxyHandler({}),
    _NoRedirectHandler(),
)


def _b64url_decode(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * ((4 - len(s) % 4) % 4))


def _mask_phone(phone: str) -> str:
    """手机号打码用于展示:138****3548。"""
    if len(phone) >= 7:
        return phone[:3] + "****" + phone[-4:]
    return "***" if phone else ""


class PlatformClient:
    """只读行情客户端:登录 → 拿 JWT → 拉真实行情。"""

    def __init__(
        self,
        base_url: str,
        phone: str = "",
        password: str = "",
        state_dir: str = "~/.echo/data/paper_trading",
        timeout: float = 12.0,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.phone = phone or os.environ.get(ENV_PHONE, "")
        self.password = password or os.environ.get(ENV_PASSWORD, "")
        self.timeout = timeout
        self.state_dir = Path(state_dir).expanduser()
        self._token: str | None = None

    # ── 凭证 ─────────────────────────────────────────────

    @classmethod
    def from_config(
        cls,
        base_url: str,
        phone: str = "",
        password: str = "",
        state_dir: str = "~/.echo/data/paper_trading",
        credentials_file: str = "~/.echo/data/paper_trading/credentials.json",
    ) -> PlatformClient:
        """从配置 + 可选凭证文件加载。凭证文件 {phone, password},chmod 600。"""
        client = cls(base_url, phone, password, state_dir)
        path = Path(credentials_file).expanduser()
        if path.exists():
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                client.phone = client.phone or str(data.get("phone", ""))
                client.password = client.password or str(data.get("password", ""))
            except Exception as exc:  # noqa: BLE001
                _logger.warning("paper_trading: 凭证文件读取失败: %s", exc)
        return client

    @property
    def token_path(self) -> Path:
        return self.state_dir / "token.json"

    @property
    def has_credentials(self) -> bool:
        return bool(self.phone and self.password)

    @property
    def account_name(self) -> str:
        """从当前 JWT 解码平台账号名(如 HL51550949),未登录返回空串。"""
        if not self._token:
            return ""
        try:
            claims = json.loads(_b64url_decode(self._token.split(".")[1]))
            return str(claims.get("account") or "")
        except Exception:  # noqa: BLE001
            return ""

    def save_credentials(
        self,
        phone: str,
        password: str,
        credentials_file: str = "~/.echo/data/paper_trading/credentials.json",
    ) -> Path:
        """把平台账号凭证写到本地文件(chmod 600)。只落盘,不校验。"""
        path = Path(credentials_file).expanduser()
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(
            json.dumps({"phone": phone, "password": password}, ensure_ascii=False),
            encoding="utf-8",
        )
        tmp.chmod(0o600)
        tmp.replace(path)
        path.chmod(0o600)
        self.phone = phone
        self.password = password
        return path

    def clear_credentials(
        self,
        credentials_file: str = "~/.echo/data/paper_trading/credentials.json",
    ) -> None:
        """删除本地凭证文件,并清空客户端内存中的账号。"""
        path = Path(credentials_file).expanduser()
        try:
            if path.exists():
                path.unlink()
        except Exception as exc:  # noqa: BLE001
            _logger.warning("paper_trading: 清除凭证失败: %s", exc)
        self.phone = ""
        self.password = ""
        self._token = None

    # ── 基础 HTTP ────────────────────────────────────────

    def _request(
        self,
        method: str,
        path: str,
        payload: dict[str, Any] | None = None,
        *,
        auth: bool = True,
    ) -> dict[str, Any]:
        """发一次带鉴权的请求;若平台判定会话失效(code 20040 等),自动重登后重试一次。"""
        resp = self._request_once(method, path, payload, auth=auth)
        if auth and isinstance(resp, dict) and resp.get("code") in _AUTH_EXPIRED_CODES:
            _logger.warning("paper_trading: 会话失效(%s),强制重登后重试 %s", resp.get("code"), path)
            try:
                self.login(force=True)
            except Exception as exc:  # noqa: BLE001
                raise PlatformClientError(f"重登失败: {exc}") from exc
            resp = self._request_once(method, path, payload, auth=auth)
        return resp

    def _request_once(
        self,
        method: str,
        path: str,
        payload: dict[str, Any] | None = None,
        *,
        auth: bool = True,
    ) -> dict[str, Any]:
        if not secure_upstream_origin(self.base_url):
            raise PlatformClientError("平台请求仅允许 HTTPS 上游")
        headers = {"User-Agent": "okhttp/4.9.0"}
        data = None
        if payload is not None:
            data = json.dumps(payload).encode()
            headers["Content-Type"] = "application/json"
        if auth and self._token:
            # 平台用 `token` 请求头鉴权(部分接口也认 Authorization)
            headers["token"] = self._token
            headers["Authorization"] = "Bearer " + self._token
        req = urllib.request.Request(
            self.base_url + path, data=data, headers=headers, method=method
        )
        try:
            # Default urllib follows 30x responses and can carry our platform
            # token/Authorization to another host or even plain HTTP.  Reject
            # every redirect; callers receive a normal platform error instead.
            with _NO_REDIRECT_OPENER.open(req, timeout=self.timeout) as resp:
                body = resp.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            raise PlatformClientError(f"HTTP {exc.code} {path}: {exc.read()[:200]}") from exc
        except Exception as exc:  # noqa: BLE001
            raise PlatformClientError(f"网络错误 {path}: {exc}") from exc
        try:
            return json.loads(body)
        except ValueError as exc:
            raise PlatformClientError(f"响应非 JSON {path}: {body[:120]}") from exc

    # ── 登录 ─────────────────────────────────────────────

    def _load_token(self) -> str | None:
        try:
            if self.token_path.exists():
                data = json.loads(self.token_path.read_text(encoding="utf-8"))
                tok = data.get("token")
                if tok:
                    # 粗略检查未过期(读 JWT exp)
                    try:
                        payload = _b64url_decode(tok.split(".")[1])
                        claims = json.loads(payload)
                        exp = int(claims.get("exp", 0))
                        if exp > 0 and exp < 1760000000:  # 明显过期则重登
                            return None
                    except Exception:  # noqa: BLE001
                        return tok
                    return tok
        except Exception as exc:  # noqa: BLE001
            _logger.warning("paper_trading: token 读取失败: %s", exc)
        return None

    def _save_token(self, token: str) -> None:
        try:
            self.state_dir.mkdir(parents=True, exist_ok=True)
            tmp = self.token_path.with_suffix(".tmp")
            tmp.write_text(json.dumps({"token": token}), encoding="utf-8")
            tmp.replace(self.token_path)
            try:
                tmp.chmod(0o600)
                self.token_path.chmod(0o600)
            except Exception as exc:  # pragma: no cover
                _logger.debug("paper_trading: token 权限收紧失败", exc_info=exc)
        except Exception as exc:  # noqa: BLE001
            _logger.warning("paper_trading: token 保存失败: %s", exc)

    def login(self, force: bool = False) -> str:
        """登录拿 JWT。成功缓存 token;返回 token 字符串。"""
        if not force:
            cached = self._load_token()
            if cached:
                self._token = cached
                return cached
        if not self.has_credentials:
            raise PlatformClientError(
                f"未配置平台账号:请设置环境变量 {ENV_PHONE}/{ENV_PASSWORD} "
                "或在 ~/.echo/data/paper_trading/credentials.json 提供 {phone,password}"
            )
        if not HAS_CRYPTO:
            raise PlatformClientError("缺少 cryptography 库,无法做 RSA 加密")

        # 1) 拉 RSA 公钥
        resp = self._request("POST", "/system/systemConfigs/getPublicKey", {}, auth=False)
        if resp.get("code") != 1:
            raise PlatformClientError(f"获取公钥失败: {resp}")
        pub_b64 = (resp.get("data") or {}).get("publicKey", "")
        if not pub_b64:
            raise PlatformClientError("公钥为空")
        pem = (
            "-----BEGIN PUBLIC KEY-----\n"
            + "\n".join(pub_b64[i : i + 64] for i in range(0, len(pub_b64), 64))
            + "\n-----END PUBLIC KEY-----\n"
        )
        pub = serialization.load_pem_public_key(pem.encode("utf-8"))

        # 2) RSA 加密密码
        encrypted = base64.b64encode(
            pub.encrypt(self.password.encode("utf-8"), padding.PKCS1v15())
        ).decode()

        # 3) 登录
        resp = self._request(
            "POST",
            "/member/member/login",
            {"phone": self.phone, "loginPassword": encrypted},
            auth=False,
        )
        if resp.get("code") != 1:
            raise PlatformClientError(f"登录失败: {resp}")
        data = resp.get("data") or {}
        token = data.get("token")
        if not token:
            raise PlatformClientError(f"登录响应无 token: {resp}")
        self._token = token
        self._save_token(token)
        return token

    def _ensure_token(self) -> str:
        if self._token:
            return self._token
        return self.login()

    # ── 会员 / 账户(只读) ─────────────────────────────────

    @property
    def member_id(self) -> str:
        """从 JWT 解码 memberId。"""
        if not self._token:
            return ""
        try:
            claims = json.loads(_b64url_decode(self._token.split(".")[1]))
            return str(claims.get("memberId") or "")
        except Exception:  # noqa: BLE001
            return ""

    def get_member_info(self) -> dict[str, Any]:
        self._ensure_token()
        return (
            self._request(
                "POST",
                "/member/member/getMemberBaseInfo",
                {"memberId": self.member_id, "account": self.account_name or self.phone},
            ).get("data")
            or {}
        )

    def list_contracts(self) -> list[dict[str, Any]]:
        """当前合约列表(轻量):contractId/contractName/amountAvailable/totalTradersMoney。"""
        self._ensure_token()
        resp = self._request(
            "POST", "/contract/ContractListMember/memberId", {"memberId": self.member_id}
        )
        if resp.get("code") != 1:
            raise PlatformClientError(f"合约列表失败: {resp}")
        return resp.get("data") or []

    def contract_list_full(self) -> list[dict[str, Any]]:
        """完整合约列表(含账户汇总字段:合约总值/浮动盈亏/预警线/合约编号等)。"""
        self._ensure_token()
        resp = self._request("POST", "/contract/ContractList/select", {"memberId": self.member_id})
        if resp.get("code") != 1:
            raise PlatformClientError(f"合约详情失败: {resp}")
        data = resp.get("data") or {}
        return data.get("contractListVOS") or []

    def contract_details(self, contract_id: str) -> dict[str, Any]:
        """单个合约详情(尽力而为;接口偶发 40013 时降级返回空)。"""
        self._ensure_token()
        try:
            resp = self._request(
                "POST",
                "/contract/ContractList/contractDetails",
                {
                    "token": self._token,
                    "memberId": self.member_id,
                    "id": contract_id,
                    "contractStats": 0,
                },
            )
            if resp.get("code") == 1:
                return resp.get("data") or {}
            _logger.warning("paper_trading: 合约详情接口返回 %s", resp)
        except Exception as exc:  # noqa: BLE001
            _logger.warning("paper_trading: 合约详情拉取失败: %s", exc)
        return {}

    def positions(self) -> Any:
        """当前持仓(返回持仓列表)。"""
        self._ensure_token()
        resp = self._request("POST", "/stock/position", {"token": self._token})
        if resp.get("code") != 1:
            raise PlatformClientError(f"持仓失败: {resp}")
        return self._maybe_gunzip(resp.get("data"))

    def orders(
        self, contract_id: str = "", type_: int = 1, current: int = 1, size: int = 20
    ) -> dict[str, Any]:
        """委托/成交记录(type_=1 已成交,2 已撤单)。

        平台对非空响应返回 gzip 的 ``{stockOrderVOList:[...], pages:N}``,空记录返回 ``[]``。
        统一归一化为 ``{"list":[...], "pages":N, "total":N, "type":type_}``。
        """
        self._ensure_token()
        payload: dict[str, Any] = {
            "token": self._token,
            "memberId": self.member_id,
            "type": type_,
            "current": current,
            "size": size,
        }
        if contract_id:
            payload["contractId"] = contract_id
        resp = self._request("POST", "/stock/stockOrder", payload)
        if resp.get("code") != 1:
            raise PlatformClientError(f"委托记录失败: {resp}")
        data = self._maybe_gunzip(resp.get("data"))
        if isinstance(data, dict):
            items = data.get("stockOrderVOList") or data.get("list") or []
            pages = int(data.get("pages") or 1)
        elif isinstance(data, list):
            items, pages = data, 1
        else:
            items, pages = [], 1
        return {
            "list": items,
            "pages": max(1, pages),
            "total": len(items),
            "type": type_,
        }

    def money_records(
        self,
        contract_id: str = "",
        type_: int | str = "",
        date: str = "",
        current: int = 1,
        size: int = 20,
    ) -> list[dict[str, Any]]:
        """资金流水/交易明细(申请/买入成功/卖出成功/提盈/结算等)。"""
        self._ensure_token()
        payload: dict[str, Any] = {
            "token": self._token,
            "memberId": self.member_id,
            "contractId": contract_id or "",
            "type": type_,
            "date": date,
            "current": current,
            "size": size,
        }
        resp = self._request("POST", "/contract/ContractList/getContractMoneyRecord", payload)
        if resp.get("code") != 1:
            raise PlatformClientError(f"资金流水失败: {resp}")
        data = self._maybe_gunzip(resp.get("data"))
        if isinstance(data, dict):
            return data.get("moneyRecordVOS") or data.get("list") or []
        return data if isinstance(data, list) else []

    def rate_table(self) -> list[dict[str, Any]]:
        """配资费率表(倍数/按天/按周/按月利率)。"""
        self._ensure_token()
        resp = self._request("POST", "/contract/getContractRateTable", {"memberId": self.member_id})
        if resp.get("code") != 1:
            raise PlatformClientError(f"费率表失败: {resp}")
        return resp.get("data") or []

    def apply_options(self) -> dict[str, Any]:
        """申请资金档位/类型(按天/按周/按月 + 保证金档位 + 倍数)。"""
        self._ensure_token()
        resp = self._request("POST", "/contract/system/type", {"memberId": self.member_id})
        if resp.get("code") != 1:
            raise PlatformClientError(f"申请选项失败: {resp}")
        return resp.get("data") or {}

    def sell_panel(self, contract_id: str, stock_code: str) -> dict[str, Any]:
        """卖出面板(只读):可卖数量 + 各项费率。"""
        self._ensure_token()
        resp = self._request(
            "POST",
            "/stock/SellStock/show",
            {
                "contractId": contract_id,
                "memberId": self.member_id,
                "stockCode": stock_code,
            },
        )
        if resp.get("code") != 1:
            raise PlatformClientError(f"卖出面板失败: {resp}")
        return resp.get("data") or {}

    # ── 合约/交易操作(真实,仅在用户明确触发时调用) ────────

    def apply_contract(
        self,
        contract_type: int,
        principal: float,
        multiple: int,
    ) -> dict[str, Any]:
        """申请/扩大配资合约。contract_type: 1按天 2按周 3按月(与平台一致)。"""
        self._ensure_token()
        return self._request(
            "POST",
            "/contract/applycontract/add",
            {
                "contractType": int(contract_type),
                "principal": float(principal),
                "multiple": int(multiple),
                "memberId": self.member_id,
            },
        )

    def buy(
        self,
        contract_id: str,
        stock_code: str,
        stock_name: str,
        entrust_type: int,
        price: float | None,
        number: int,
    ) -> dict[str, Any]:
        """买入(真实下单)。entrust_type: 0限价 1市价;number 须为 100 整数倍。"""
        self._ensure_token()
        payload: dict[str, Any] = {
            "contractId": contract_id,
            "memberId": self.member_id,
            "stockCode": stock_code,
            "stockName": stock_name,
            "entrustType": str(int(entrust_type)),
            "entrustNumber": int(number),
        }
        if entrust_type == 0:
            payload["entrustPrice"] = float(price) if price else 0.0
        return self._request("POST", "/stock/BuyStock/insert", payload)

    def sell(
        self,
        contract_id: str,
        stock_code: str,
        stock_name: str,
        entrust_type: int,
        price: float | None,
        number: int,
    ) -> dict[str, Any]:
        """卖出(真实下单)。"""
        self._ensure_token()
        payload: dict[str, Any] = {
            "contractId": contract_id,
            "memberId": self.member_id,
            "stockCode": stock_code,
            "stockName": stock_name,
            "entrustType": str(int(entrust_type)),
            "entrustNumber": int(number),
        }
        if entrust_type == 0:
            payload["entrustPrice"] = float(price) if price else 0.0
        return self._request("POST", "/stock/SellStock/insert", payload)

    def cancel_order(self, order_id: str, contract_id: str) -> dict[str, Any]:
        """撤单。"""
        self._ensure_token()
        return self._request(
            "POST",
            "/stock/cancelOrder/cancel",
            {
                "orderId": order_id,
                "contractId": contract_id,
                "memberId": self.member_id,
            },
        )

    def add_capital(self, contract_id: str, money: float) -> dict[str, Any]:
        """追加资金(扩大合约可用资金)。"""
        self._ensure_token()
        return self._request(
            "POST",
            "/contract/appendcapital/additional",
            {"contractId": contract_id, "memberId": self.member_id, "money": float(money)},
        )

    def withdraw_profit(self, contract_id: str, money: float) -> dict[str, Any]:
        """提盈。"""
        self._ensure_token()
        return self._request(
            "POST",
            "/contract/WithdrawProfit/extract",
            {"contractId": contract_id, "memberId": self.member_id, "money": float(money)},
        )

    # ── 行情 ─────────────────────────────────────────────

    @staticmethod
    def _gunzip_b64(data: str) -> dict[str, Any]:
        raw = base64.b64decode(data)
        try:
            raw = gzip.decompress(raw)
        except Exception as exc:  # noqa: BLE001
            raise PlatformClientError(f"gzip 解压失败: {exc}") from exc
        try:
            return json.loads(raw.decode("utf-8"))
        except ValueError as exc:
            raise PlatformClientError(f"行情数据非 JSON: {raw[:120]}") from exc

    @staticmethod
    def _maybe_gunzip(data: Any) -> Any:
        """有些接口返回 gzip base64 字符串,有些直接返回 JSON(list/dict)。"""
        if not isinstance(data, str):
            return data
        if data.startswith("H4sI"):  # base64 gzip magic
            return PlatformClient._gunzip_b64(data)
        try:
            return json.loads(data)
        except ValueError:
            return data

    def fetch_today_stock(self) -> dict[str, Any]:
        """大盘概览:真实指数 + 全市场涨跌家数 + 市场状态。"""
        resp = self._request("POST", "/market/v2/data/doAction?event=todayStock", {})
        if resp.get("code") != 1:
            raise PlatformClientError(f"todayStock 失败: {resp}")
        return self._gunzip_b64(resp.get("data", ""))

    def fetch_brief(self, code: str) -> dict[str, Any]:
        """单只股票公司简况(真实基本面)。code 形如 600519.sh。"""
        resp = self._request("GET", f"/market/brief?code={code}")
        if resp.get("code") != 1:
            raise PlatformClientError(f"market/brief 失败: {resp}")
        return resp.get("data") or {}

    def fetch_stock_choose(self) -> list[dict[str, Any]]:
        """平台自选列表(带实时行情:现价/涨跌幅/量/分时)。

        对应前端 ``getStockChooseV2`` -> ``POST /stock/stockCodeV2``(gzip)。
        未登录态会由 ``_ensure_token`` 兜底;失败抛 :class:`PlatformClientError`。
        """
        self._ensure_token()
        resp = self._request(
            "POST",
            "/stock/stockCodeV2",
            {"memberId": self.member_id, "event": "subscribe", "isCompress": True},
        )
        if resp.get("code") != 1:
            raise PlatformClientError(f"自选失败: {resp}")
        data = self._maybe_gunzip(resp.get("data"))
        if isinstance(data, dict):
            return data.get("optionalVOList") or []
        return []

    def fetch_real_quotes(self, codes: list[str]) -> list[dict[str, Any]]:
        """单股实时报价(现价/涨跌幅/涨跌停/分时等)。

        对应前端 ``kLineRealTime`` -> ``doAction?event=kLineRealTime``(gzip)。
        ``codes`` 形如 ["600519.sh", "003032.sz"];失败抛 :class:`PlatformClientError`。
        """
        self._ensure_token()
        resp = self._request(
            "POST",
            "/market/v2/data/doAction?event=kLineRealTime",
            {"url": "kLineRealTime", "event": "subscribe", "params": list(codes or [])},
        )
        if resp.get("code") != 1:
            raise PlatformClientError(f"实时报价失败: {resp}")
        data = self._maybe_gunzip(resp.get("data"))
        if isinstance(data, list):
            return data
        # The upstream returns one quote object for a single symbol and a list
        # for multiple symbols.  Keep the public contract stable for callers.
        if isinstance(data, dict) and data.get("stockCode"):
            return [data]
        return []


__all__ = ["PlatformClient", "PlatformClientError"]


# ── 可选实时行情源 ────────────────────────────────────────

DEFAULT_BASE_URL = "http://114.66.32.152:58868/api"


class LiveDataSource:
    """可选的真实行情源(只读):包装 :class:`PlatformClient`,带缓存 TTL 与优雅降级。

    页面/API 每几秒刷新一次,但本类会按 ``ttl`` 合并请求,避免高频打对方后端。
    登录失败 / 无凭证 / 网络异常一律降级返回 ``{available: False, ...}``,
    绝不抛出异常、绝不影响本地模拟交易功能。
    """

    def __init__(
        self,
        client: PlatformClient,
        ttl: float = 30.0,
        credentials_file: str = "~/.echo/data/paper_trading/credentials.json",
    ) -> None:
        self._client = client
        self._ttl = max(1.0, float(ttl))
        self._credentials_file = credentials_file
        self._cache: dict[str, Any] | None = None
        self._cached_at = 0.0
        self._watch_cache: dict[str, Any] | None = None
        self._watch_cached_at = 0.0
        self._watch_ttl = max(2.0, min(self._ttl, 10.0))
        self._lock = threading.Lock()

    @classmethod
    def from_config(
        cls,
        cfg: dict[str, Any],
        state_dir: str = "~/.echo/data/paper_trading",
        credentials_file: str = "~/.echo/data/paper_trading/credentials.json",
    ) -> LiveDataSource:
        base_url = str(cfg.get("base_url") or DEFAULT_BASE_URL)
        ttl = float(cfg.get("live_ttl") or 30.0)
        client = PlatformClient.from_config(
            base_url, state_dir=state_dir, credentials_file=credentials_file
        )
        return cls(client, ttl=ttl, credentials_file=credentials_file)

    @property
    def client(self) -> PlatformClient:
        return self._client

    @property
    def available(self) -> bool:
        return self._client.has_credentials

    @property
    def configured(self) -> bool:
        """本地是否已保存凭证(文件存在或客户端内存有账号)。"""
        if self._client.has_credentials:
            return True
        return Path(self._credentials_file).expanduser().exists()

    @property
    def phone(self) -> str:
        return self._client.phone or ""

    @property
    def account(self) -> str:
        """展示用账号:优先 JWT 里的 account,否则打码手机号。"""
        return self._client.account_name or _mask_phone(self.phone)

    def save_credentials(self, phone: str, password: str) -> dict[str, Any]:
        """保存平台凭证到本地(chmod 600)并尝试登录验证。"""
        phone = (phone or "").strip()
        password = password or ""
        if not phone or not password:
            return {"saved": False, "ok": False, "error": "手机号和密码不能为空"}
        self._client.save_credentials(phone, password, self._credentials_file)
        with self._lock:
            self._cache = None
            self._cached_at = 0.0
        try:
            self._client.login(force=True)
            return {
                "ok": True,
                "saved": True,
                "verified": True,
                "account": self._client.account_name or _mask_phone(phone),
            }
        except Exception as exc:  # noqa: BLE001 — 已落盘,登录验证失败单独提示
            _logger.warning("paper_trading: 凭证已保存但登录验证失败: %s", exc)
            return {
                "ok": True,
                "saved": True,
                "verified": False,
                "account": _mask_phone(phone),
                "error": str(exc),
            }

    def clear_credentials(self) -> dict[str, Any]:
        """删除本地凭证文件并清空内存账号。"""
        self._client.clear_credentials(self._credentials_file)
        with self._lock:
            self._cache = None
            self._cached_at = 0.0
        return {"ok": True, "message": "已清除平台凭证"}

    def overview(self, force: bool = False) -> dict[str, Any]:
        """实时大盘概览(带缓存)。失败降级,不抛异常。"""
        now = time.time()
        if not force and self._cache and now - self._cached_at < self._ttl:
            return self._cache
        with self._lock:
            if not force and self._cache and time.time() - self._cached_at < self._ttl:
                return self._cache
            self._cache = self._build_overview()
            self._cached_at = time.time()
            return self._cache

    def watch(self, force: bool = False) -> dict[str, Any]:
        """盯盘聚合:大盘 + 平台持仓 + 平台自选(全部真实行情)。

        独立短 TTL(``_watch_ttl``,2~10s)让盯盘更实时,但不会比大盘的
        ``_ttl`` 更激进。任一来源失败只降级对应字段,不抛异常。
        """
        now = time.time()
        if not force and self._watch_cache and now - self._watch_cached_at < self._watch_ttl:
            return self._watch_cache
        with self._lock:
            if (
                not force
                and self._watch_cache
                and time.time() - self._watch_cached_at < self._watch_ttl
            ):
                return self._watch_cache
            self._watch_cache = self._build_watch()
            self._watch_cached_at = time.time()
            return self._watch_cache

    # ── 内部 ─────────────────────────────────────────────

    def _build_overview(self) -> dict[str, Any]:
        try:
            self._client.login()  # token 未过期则不发网络请求
            raw = self._client.fetch_today_stock()
        except Exception as exc:  # noqa: BLE001 — 降级,不让页面/下单受影响
            _logger.warning("paper_trading: 实时行情拉取失败(降级): %s", exc)
            return {
                "available": False,
                "source": self._client.base_url,
                "status": "",
                "fetched_at": "",
                "error": str(exc),
                "indices": [],
                "breadth": {"up": 0, "down": 0, "unchanged": 0, "stop": 0},
            }
        indices: list[dict[str, Any]] = []
        for s in raw.get("stockVOS") or []:
            price = s.get("price")
            if price is None:
                continue
            prev = s.get("yClose")
            chg = s.get("risefall")
            pct = s.get("increase")
            indices.append(
                {
                    "symbol": s.get("symbol") or "",
                    "name": s.get("name") or s.get("symbol") or "",
                    "price": round(float(price), 2),
                    "prev_close": round(float(prev), 2) if prev is not None else None,
                    "change": round(float(chg), 2) if chg is not None else None,
                    "change_pct": round(float(pct), 2) if pct is not None else None,
                    "spark": [round(float(x), 2) for x in (s.get("increases") or [])],
                }
            )
        return {
            "available": True,
            "source": self._client.base_url,
            "status": raw.get("stockStatus") or "",
            "fetched_at": datetime.now().isoformat(timespec="seconds"),
            "indices": indices,
            "breadth": {
                "up": int(raw.get("up") or 0),
                "down": int(raw.get("down") or 0),
                "unchanged": int(raw.get("unchanged") or 0),
                "stop": int(raw.get("stop") or 0),
            },
        }

    def _build_watch(self) -> dict[str, Any]:
        """盯盘数据聚合:大盘 + 平台持仓 + 平台自选(真实行情)。

        三个来源各自降级:大盘走 ``_build_overview``(已有降级),持仓/自选
        失败只把对应字段置空并带 warning,不拖垮整个盯盘页。
        """
        try:
            self._client.login()  # token 未过期则不发网络请求
        except Exception as exc:  # noqa: BLE001
            _logger.warning("paper_trading: 盯盘登录失败(降级): %s", exc)
            return {
                "available": False,
                "source": self._client.base_url,
                "status": "",
                "fetched_at": "",
                "error": str(exc),
                "indices": [],
                "breadth": {"up": 0, "down": 0, "unchanged": 0, "stop": 0},
                "positions": [],
                "watchlist": [],
            }
        overview = self._build_overview()  # 自己的缓存路径,这里直接取最新
        positions: Any = []
        watchlist: list[dict[str, Any]] = []
        try:
            positions = self._client.positions()
            if not isinstance(positions, list):
                positions = []
        except Exception as exc:  # noqa: BLE001 — 单点降级
            _logger.warning("paper_trading: 盯盘持仓拉取失败(降级): %s", exc)
            positions = []
        try:
            watchlist = self._client.fetch_stock_choose() or []
        except Exception as exc:  # noqa: BLE001 — 单点降级
            _logger.warning("paper_trading: 盯盘自选拉取失败(降级): %s", exc)
            watchlist = []
        return {
            "available": True,
            "source": self._client.base_url,
            "status": overview.get("status", ""),
            "fetched_at": datetime.now().isoformat(timespec="seconds"),
            "indices": overview.get("indices", []),
            "breadth": overview.get("breadth", {}),
            "positions": positions,
            "watchlist": watchlist,
        }


from .live_push import (  # noqa: E402 - compatibility re-exports follow client definitions
    HAS_WEBSOCKETS,  # noqa: F401 - retain the existing module attribute
    LivePushClient,
    _gunzip_json_b64,
    _normalize_push,
    _normalize_quote,
    _secure_push_endpoint,  # noqa: F401 - retain the tested private import path
    _ws_sign,
)

__all__ = [
    "PlatformClient",
    "PlatformClientError",
    "LiveDataSource",
    "LivePushClient",
    "DEFAULT_BASE_URL",
    "_mask_phone",
    "_ws_sign",
    "_gunzip_json_b64",
    "_normalize_quote",
    "_normalize_push",
]
