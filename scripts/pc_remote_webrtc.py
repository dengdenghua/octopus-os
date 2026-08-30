"""母体 WebRTC remote-desktop server.

Signaling rides the tentacle WebSocket (ws_server on_custom hook):
  phone → webrtc/request   (asks for a session)
  母体  → webrtc/offer     {sdp,type}   (screen video track + 'input' data channel)
  phone → webrtc/answer    {sdp,type}
Media (H.264/VP8 video) + input (DataChannel) then flow P2P over WebRTC (STUN hole-punch;
add a TURN server for symmetric-NAT relay). Input JSON on the channel:
  {"action":"tap|rightclick|move|down|up|scroll|type|key","x":0..1,"y":0..1,"text":...}

Deps:  uv pip install --python .venv/bin/python aiortc av pyautogui pillow mss
Run:   .venv/bin/python scripts/pc_remote_webrtc.py
macOS: grant Screen Recording + Accessibility to the controlling app.
"""
import asyncio
import json
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import av  # noqa: E402
import mss  # noqa: E402
import pyautogui  # noqa: E402
from aiortc import (  # noqa: E402
    RTCConfiguration,
    RTCIceServer,
    RTCPeerConnection,
    RTCSessionDescription,
    VideoStreamTrack,
)
from PIL import Image  # noqa: E402

from runtime.tentacle.transport.ws_server import TentacleWebSocketServer  # noqa: E402

pyautogui.FAILSAFE = False
pyautogui.PAUSE = 0.0
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s", datefmt="%H:%M:%S")
log = logging.getLogger("pcwebrtc")

server: TentacleWebSocketServer | None = None
pcs: dict[str, RTCPeerConnection] = {}
W, H = pyautogui.size()
EW, EH = 1280, 720
# 多个 STUN(并行查、谁通用谁=降级切换)。只放实测可达的,避免等死服务器超时拖慢 ICE。
# 对称 NAT 仍需 TURN —— 把 turn: 项加进这个列表即可(urls/username/credential)。
STUN = [
    RTCIceServer(urls="stun:stun.cloudflare.com:3478"),
    RTCIceServer(urls="stun:stun.chat.bilibili.com:3478"),
    RTCIceServer(urls="stun:stun.l.google.com:19302"),
    RTCIceServer(urls="stun:stun.nextcloud.com:3478"),
]


class ScreenTrack(VideoStreamTrack):
    def __init__(self):
        super().__init__()
        self.sct = mss.mss()
        self.n = 0

    async def recv(self):
        pts, tb = await self.next_timestamp()
        im = None
        try:
            mons = self.sct.monitors
            mon = next((m for m in (mons[1:] + mons[:1]) if m["width"] > 0 and m["height"] > 0), None)
            if mon:
                shot = self.sct.grab(mon)
                im = Image.frombytes("RGB", shot.size, shot.bgra, "raw", "BGRX").resize((EW, EH))
        except Exception:
            self.sct = mss.mss()  # self-heal
        if im is None:
            im = Image.new("RGB", (EW, EH), (12, 28, 54))  # display asleep → placeholder
        fr = av.VideoFrame.from_image(im)
        fr.pts = pts
        fr.time_base = tb
        self.n += 1
        return fr


def apply_input(params: dict):
    action = params.get("action", "tap")
    nx = float(params.get("x", 0) or 0)
    ny = float(params.get("y", 0) or 0)
    text = params.get("text")
    px = int(nx * W)
    py = int(ny * H)
    if action == "tap":
        pyautogui.click(px, py)
    elif action == "rightclick":
        pyautogui.click(px, py, button="right")
    elif action == "move":
        pyautogui.moveTo(px, py)
    elif action == "down":
        pyautogui.mouseDown(px, py)
    elif action == "up":
        pyautogui.mouseUp(px, py)
    elif action == "scroll":
        pyautogui.scroll(int(-ny * 60))
    elif action == "type" and text:
        pyautogui.typewrite(text, interval=0.01)
    elif action == "key" and text:
        pyautogui.press(text)


async def gather_complete(pc: RTCPeerConnection):
    for _ in range(40):
        if pc.iceGatheringState == "complete":
            return
        await asyncio.sleep(0.25)


async def start_session(tentacle_id: str):
    # 关掉旧会话
    old = pcs.pop(tentacle_id, None)
    if old:
        await old.close()
    pc = RTCPeerConnection(RTCConfiguration(iceServers=STUN))
    pcs[tentacle_id] = pc
    pc.addTrack(ScreenTrack())
    chan = pc.createDataChannel("input")

    @chan.on("message")
    def on_msg(m):
        try:
            params = json.loads(m)
        except Exception:
            return
        if params.get("action") not in ("move", "scroll"):
            log.info("input %s", params)
        asyncio.ensure_future(asyncio.get_event_loop().run_in_executor(None, apply_input, params))

    @pc.on("connectionstatechange")
    def on_state():
        log.info("pc[%s] connection: %s", tentacle_id, pc.connectionState)

    await pc.setLocalDescription(await pc.createOffer())
    await gather_complete(pc)
    await server.send_json(tentacle_id, {
        "jsonrpc": "2.0", "method": "webrtc/offer",
        "params": {"sdp": pc.localDescription.sdp, "type": pc.localDescription.type},
    })
    log.info("sent webrtc/offer to %s (gather=%s)", tentacle_id, pc.iceGatheringState)


async def on_custom(tentacle_id, msg, ws):
    method = msg.get("method", "")
    params = msg.get("params", {}) or {}
    tid = tentacle_id or params.get("tentacle_id")
    if method == "webrtc/request":
        await start_session(tid)
    elif method == "webrtc/answer":
        pc = pcs.get(tid)
        if pc:
            await pc.setRemoteDescription(RTCSessionDescription(sdp=params["sdp"], type=params.get("type", "answer")))
            log.info("set remote answer for %s", tid)


async def on_hello(hello, ws):
    log.info("PHONE CONNECTED %s — awaiting webrtc/request", hello.tentacle_id)


async def main():
    global server
    # 本机/同机测试:默认 127.0.0.1(回环,免 token)。
    # 跨网(Tailscale/局域网):ECHO_TENTACLE_HOST=0.0.0.0 ECHO_TENTACLE_TOKEN=<密码> 再起。
    # 非回环绑定时 ws_server 强制要 token(token 由 ECHO_TENTACLE_TOKEN 环境变量提供)。
    import os
    host = os.environ.get("ECHO_TENTACLE_HOST", "127.0.0.1")
    server = TentacleWebSocketServer(
        host=host, port=8765,
        on_device_hello=on_hello, on_custom=on_custom, auth_token=None,
    )
    await server.start()
    tok = "yes" if os.environ.get("ECHO_TENTACLE_TOKEN") else "no"
    log.info("WebRTC PC-remote server READY  bind=%s:8765  token=%s  Mac=%dx%d  stream=%dx%d", host, tok, W, H, EW, EH)
    await asyncio.Event().wait()


asyncio.run(main())

