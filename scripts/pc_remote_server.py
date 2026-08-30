"""母体 PC-remote driver: streams the Mac screen to connected phones and applies their
remote/input as real Mac mouse/keyboard. Uses echo-agent's real TentacleWebSocketServer.

Video: H.264 (hardware h264_videotoolbox) Annex-B, streamed as type-0x01 frames; keyframe
every ~1s so a freshly-connected phone starts decoding quickly. Falls back to JPEG if no
H.264 encoder is available.
Input: tap / rightclick / move / down+move+up (drag) / scroll / type / key.

Deps (install into this repo's venv):  uv pip install --python .venv/bin/python av pyautogui pillow mss
Run:                                   .venv/bin/python scripts/pc_remote_server.py
macOS: grant Screen Recording (capture) + Accessibility (input) to the controlling app.
The phone connects via the tentacle WebSocket (ws://<host>:8765) and opens 设置→母体远程桌面.
"""
import asyncio
import io
import logging
import struct
import sys
from fractions import Fraction
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import mss  # noqa: E402
import pyautogui  # noqa: E402
from PIL import Image  # noqa: E402

pyautogui.FAILSAFE = False
pyautogui.PAUSE = 0.0

from runtime.tentacle.transport.ws_server import TentacleWebSocketServer  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s", datefmt="%H:%M:%S")
log = logging.getLogger("pcdriver")

server: TentacleWebSocketServer | None = None
EW, EH = 1280, 720       # encode/stream size (16:9)
FPS = 15
SCROLL_K = 60
W, H = pyautogui.size()  # logical size for input mapping (Retina-correct)

# 选择 H.264 编码器（优先硬件 videotoolbox），否则降级 JPEG
import av  # noqa: E402

ENC_NAME = None
for _n in ("h264_videotoolbox", "libx264"):
    try:
        av.codec.Codec(_n, "w")
        ENC_NAME = _n
        break
    except Exception:
        pass


def build_frame(ftype: int, is_key: bool, data: bytes) -> bytes:
    # 帧头: id长度(2B BE) + 类型(0x01=H264, 0x02=JPEG) + 标志(bit0=关键帧) + id + 数据
    idb = b"pc-host"
    return struct.pack(">H", len(idb)) + bytes([ftype, 0x01 if is_key else 0x00]) + idb + data


def make_encoder():
    cc = av.CodecContext.create(ENC_NAME, "w")
    cc.width = EW
    cc.height = EH
    cc.pix_fmt = "yuv420p"
    cc.bit_rate = 4_000_000
    cc.framerate = Fraction(FPS, 1)
    cc.time_base = Fraction(1, FPS)
    cc.options = {"g": str(FPS)}   # 每 ~1s 一个关键帧
    return cc


async def capture_loop():
    await asyncio.sleep(1.0)
    sct = None
    enc = make_encoder() if ENC_NAME else None
    interval = 1.0 / FPS
    pts = 0
    sent = 0
    while True:
        try:
            if sct is None:
                sct = mss.mss()
            mons = sct.monitors
            mon = mons[1] if len(mons) > 1 else mons[0]
            shot = sct.grab(mon)
            im = Image.frombytes("RGB", shot.size, shot.bgra, "raw", "BGRX").resize((EW, EH))
            if enc is not None:
                fr = av.VideoFrame.from_image(im)
                fr.pts = pts
                pts += 1
                for pkt in enc.encode(fr):
                    await server.push_pc_frame(build_frame(0x01, pkt.is_keyframe, bytes(pkt)))
                    sent += 1
                    if sent % 75 == 0:
                        log.info("sent %d H264 pkts (last %dB, key=%s)", sent, pkt.size, pkt.is_keyframe)
            else:
                buf = io.BytesIO()
                im.save(buf, "JPEG", quality=50)
                await server.push_pc_frame(build_frame(0x02, True, buf.getvalue()))
        except Exception as e:
            log.warning("capture error: %s", e)
            sct = None
        await asyncio.sleep(interval)


async def on_remote_input(tentacle_id, params):
    action = params.get("action", "tap")
    nx = float(params.get("x", 0) or 0)
    ny = float(params.get("y", 0) or 0)
    text = params.get("text")
    px = int(nx * W)
    py = int(ny * H)

    def do():
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
            pyautogui.scroll(int(-ny * SCROLL_K))
        elif action == "type" and text:
            pyautogui.typewrite(text, interval=0.01)
        elif action == "key" and text:
            pyautogui.press(text)

    if action not in ("move", "scroll"):
        log.info("手机→母体 remote/input: %s → Mac(%d,%d) text=%r", action, px, py, text)
    await asyncio.get_event_loop().run_in_executor(None, do)
    return {"ok": True, "applied": action}


async def on_hello(hello, ws):
    log.info("PHONE CONNECTED %s — streaming Mac screen (H264=%s)", hello.tentacle_id, ENC_NAME)


async def main():
    global server
    server = TentacleWebSocketServer(
        host="127.0.0.1", port=8765,
        on_device_hello=on_hello, on_remote_input=on_remote_input, auth_token=None,
    )
    await server.start()
    log.info("PC-remote driver READY  Mac=%dx%d  stream=%dx%d @%dfps  codec=%s", W, H, EW, EH, FPS, ENC_NAME or "JPEG")
    asyncio.create_task(capture_loop())
    await asyncio.Event().wait()


asyncio.run(main())


