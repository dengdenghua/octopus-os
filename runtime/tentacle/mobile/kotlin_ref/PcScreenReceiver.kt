package com.apk.claw.android.echo_mobile

import android.graphics.BitmapFactory
import android.util.Log
import android.view.MotionEvent
import android.view.View
import android.widget.ImageView
import kotlinx.coroutines.*
import kotlinx.coroutines.channels.Channel
import okhttp3.*
import okio.ByteString
import org.json.JSONObject
import java.nio.ByteBuffer
import java.util.concurrent.TimeUnit

/**
 * PC 屏幕画面接收器 —— 订阅 PC 屏幕流并在手机端显示.
 *
 * 功能：
 * 1. 通过 WebSocket 接收 PC 屏幕帧（JPEG 二进制帧）
 * 2. 解码 JPEG 并显示在 ImageView 上
 * 3. 用户触摸 ImageView 时，将归一化坐标发送回母体（远程控制 PC）
 *
 * 帧协议（与 ScreenRelay 一致）：
 *   byte 0-1   : tentacle_id 长度 (uint16 BE)
 *   byte 2     : 帧类型 (0x01=H.264, 0x02=JPEG, 0x03=WebP)
 *   byte 3     : 标志位 (bit0=关键帧)
 *   byte 4..   : tentacle_id (UTF-8)
 *   byte 4+len : 帧数据
 *
 * 远程输入协议：
 *   POST /api/tentacle/remote-input
 *   { "action": "tap", "x": 0.5, "y": 0.3 }
 *
 * 使用方法：
 *
 *   val receiver = PcScreenReceiver("ws://192.168.1.100:8765", "android-abc123")
 *   receiver.targetView = findViewById(R.id.pc_screen_view)
 *   receiver.onRemoteInput = { action, x, y ->
 *       receiver.sendRemoteInputViaHttp(action, x, y)
 *   }
 *   receiver.start()
 */
class PcScreenReceiver(
    private val runtimeUrl: String,
    private val tentacleId: String,
    private val httpClient: OkHttpClient = defaultHttpClient()
) {
    private val tag = "PcScreenReceiver"

    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.Main)
    private var webSocket: WebSocket? = null

    /** 显示 PC 画面的 ImageView */
    var targetView: ImageView? = null

    /** 远程输入回调：用户触摸时调用，发送输入事件到母体 */
    var onRemoteInput: ((action: String, x: Float, y: Float) -> Unit)? = null

    /** 帧计数 */
    @Volatile
    var frameCount: Int = 0
        private set

    /** 是否已连接 */
    @Volatile
    var isConnected: Boolean = false
        private set

    /** 帧通道（解耦接收和解码） */
    private val frameChannel = Channel<ByteArray>(capacity = 5)

    /** 解码协程 Job */
    private var decodeJob: Job? = null

    // ── 生命周期 ──────────────────────────────────────────

    /**
     * 启动 PC 屏幕流接收.
     *
     * 连接母体的 pc-screen/stream WebSocket 端点，接收二进制帧.
     */
    fun start() {
        Log.i(tag, "starting PC screen receiver: $runtimeUrl")

        // 推导 Dashboard URL（dashboard port = ws port + 1）
        val wsPort = extractPort(runtimeUrl)
        val dashboardPort = wsPort + 1
        val host = extractHost(runtimeUrl)
        val wsUrl = "ws://$host:$dashboardPort/api/tentacle/pc-screen/stream"

        val request = Request.Builder().url(wsUrl).build()

        val listener = object : WebSocketListener() {
            override fun onOpen(webSocket: WebSocket, response: Response) {
                Log.i(tag, "PC screen stream connected")
                this@PcScreenReceiver.webSocket = webSocket
                isConnected = true
            }

            override fun onMessage(webSocket: WebSocket, bytes: ByteString) {
                // 二进制帧：PC 屏幕画面
                scope.launch {
                    frameChannel.send(bytes.toByteArray())
                }
            }

            override fun onMessage(webSocket: WebSocket, text: String) {
                // 忽略文本消息
            }

            override fun onClosing(webSocket: WebSocket, code: Int, reason: String) {
                webSocket.close(code, reason)
            }

            override fun onClosed(webSocket: WebSocket, code: Int, reason: String) {
                Log.i(tag, "PC screen stream closed")
                isConnected = false
            }

            override fun onFailure(webSocket: WebSocket, t: Throwable, response: Response?) {
                Log.w(tag, "PC screen stream failure: ${t.message}")
                isConnected = false
            }
        }

        httpClient.newWebSocket(request, listener)

        // 启动解码协程
        decodeJob = scope.launch {
            for (frameData in frameChannel) {
                try {
                    decodeAndDisplay(frameData)
                } catch (e: Exception) {
                    Log.w(tag, "decode frame error: ${e.message}")
                }
            }
        }

        // 设置触摸监听
        setupTouchListener()
    }

    /**
     * 停止 PC 屏幕流接收.
     */
    fun stop() {
        Log.i(tag, "stopping PC screen receiver")
        webSocket?.close(1000, "client stop")
        webSocket = null
        isConnected = false
        decodeJob?.cancel()
        decodeJob = null
        frameChannel.close()
        targetView?.setOnTouchListener(null)
    }

    // ── 帧解码 ──────────────────────────────────────────

    /**
     * 解码二进制帧并显示.
     *
     * 帧格式：
     *   byte 0-1: tentacle_id 长度 (uint16 BE)
     *   byte 2: 帧类型 (0x02=JPEG, 0x03=WebP)
     *   byte 3: 标志位
     *   byte 4+: tentacle_id
     *   byte 4+len+: 帧数据
     */
    private fun decodeAndDisplay(data: ByteArray) {
        if (data.size < 4) return

        val buffer = ByteBuffer.wrap(data)
        val idLen = buffer.short.toInt() and 0xFFFF
        val frameType = buffer.get().toInt() and 0xFF
        val flags = buffer.get().toInt() and 0xFF

        if (data.size < 4 + idLen) return

        val payloadStart = 4 + idLen
        if (payloadStart >= data.size) return

        val payload = data.copyOfRange(payloadStart, data.size)

        // 只处理 JPEG (0x02) 和 WebP (0x03)
        if (frameType != 0x02 && frameType != 0x03) return

        val bitmap = BitmapFactory.decodeByteArray(payload, 0, payload.size) ?: return

        frameCount++

        // 在主线程显示
        scope.launch(Dispatchers.Main) {
            targetView?.setImageBitmap(bitmap)
        }
    }

    // ── 触摸转发 ──────────────────────────────────────────

    /**
     * 设置触摸监听：用户点击 ImageView 时，发送远程输入事件.
     */
    private fun setupTouchListener() {
        scope.launch(Dispatchers.Main) {
            targetView?.setOnTouchListener { view, event ->
                when (event.action) {
                    MotionEvent.ACTION_DOWN -> {
                        sendTapEvent(view, event.x, event.y)
                    }
                }
                true
            }
        }
    }

    /**
     * 发送 tap 远程输入事件.
     *
     * 将触摸坐标归一化为 [0,1] 范围，通过回调发送.
     */
    private fun sendTapEvent(view: View, x: Float, y: Float) {
        val nx = (x / view.width).coerceIn(0f, 1f)
        val ny = (y / view.height).coerceIn(0f, 1f)
        onRemoteInput?.invoke("tap", nx, ny)
    }

    /**
     * 通过 HTTP 发送远程输入事件到母体.
     *
     * 使用 POST /api/tentacle/remote-input 端点.
     */
    fun sendRemoteInputViaHttp(action: String, x: Float, y: Float) {
        scope.launch(Dispatchers.IO) {
            try {
                val wsPort = extractPort(runtimeUrl)
                val dashboardPort = wsPort + 1
                val host = extractHost(runtimeUrl)
                val url = "http://$host:$dashboardPort/api/tentacle/remote-input"

                val json = JSONObject().apply {
                    put("action", action)
                    put("x", x.toDouble())
                    put("y", y.toDouble())
                }

                val body = RequestBody.create(
                    "application/json".toMediaTypeOrNull(),
                    json.toString()
                )
                val request = Request.Builder()
                    .url(url)
                    .post(body)
                    .build()

                httpClient.newCall(request).execute().use { response ->
                    if (!response.isSuccessful) {
                        Log.w(tag, "remote input failed: ${response.code}")
                    }
                }
            } catch (e: Exception) {
                Log.w(tag, "remote input error: ${e.message}")
            }
        }
    }

    // ── 辅助 ──────────────────────────────────────────────

    private fun extractPort(url: String): Int {
        val regex = Regex(":(\\d+)")
        val match = regex.findAll(url).lastOrNull()
        return match?.groupValues?.get(1)?.toIntOrNull() ?: 8765
    }

    private fun extractHost(url: String): String {
        val regex = Regex("://([^:/]+)")
        val match = regex.find(url)
        return match?.groupValues?.get(1) ?: "localhost"
    }

    companion object {
        private fun defaultHttpClient(): OkHttpClient = OkHttpClient.Builder()
            .pingInterval(20, TimeUnit.SECONDS)
            .connectTimeout(10, TimeUnit.SECONDS)
            .readTimeout(0, TimeUnit.MILLISECONDS)
            .build()

        private fun String.toMediaTypeOrNull(): MediaType? =
            MediaType.parse(this)
    }
}
