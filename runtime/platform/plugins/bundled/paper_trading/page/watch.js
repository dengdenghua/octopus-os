(function () {
  "use strict";

  window.__quoteHubV2 = true;

  var STRUCTURE_URL = "/api/plugins/paper-trading/live/watch";
  var QUOTE_PATH = "/api/plugins/paper-trading/quotes";
  var QUOTE_URL = QUOTE_PATH;
  var MAX_CODES = 100;
  var RENDER_BATCH_MS = 150;
  var TH = 3;
  var structureTimer = null;
  var fallbackTimer = null;
  var renderTimer = null;
  var streamAbort = null;
  var streamRunId = 0;
  var reconnectTimer = null;
  var reconnectAttempt = 0;
  var serverRetryMs = 3000;
  var bearerToken = "";
  var parentOrigin = "";
  var bootTimer = null;
  var booted = false;
  var streamKey = "";
  var desiredCodes = [];
  var latestQuotes = {};
  var latestSeq = {};
  var sourceLabels = {
    platform_ws: "平台直连",
    platform_rest: "平台快照备用",
    tdx: "通达信备用",
    westock: "腾讯自选股备用"
  };
  var lastData = {
    status: "",
    indices: [],
    breadth: {},
    positions: [],
    watchlist: [],
    fetched_at: ""
  };

  var $ = function (id) { return document.getElementById(id); };
  var esc = function (value) {
    return String(value == null ? "" : value)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#39;");
  };
  var baseCode = function (value) {
    return String(value || "").trim().toLowerCase().split(".")[0];
  };
  var fmt = function (value, digits) {
    var number = Number(value);
    return isNaN(number) ? "--" : number.toFixed(digits == null ? 2 : digits);
  };
  var cls = function (pct) {
    var number = Number(pct);
    return isNaN(number) ? "flat" : (number > 0 ? "up" : (number < 0 ? "down" : "flat"));
  };
  var sign = function (value) { return Number(value) > 0 ? "+" : ""; };

  function parsedOrigin(value) {
    try {
      return new URL(value).origin;
    } catch (_err) {
      return "";
    }
  }

  function isLoopbackOrigin(origin) {
    try {
      var url = new URL(origin);
      return /^https?:$/.test(url.protocol) &&
        (url.hostname === "127.0.0.1" || url.hostname === "localhost");
    } catch (_err) {
      return false;
    }
  }

  function isTrustedParentOrigin(origin) {
    // Opaque origins stringify to "null".  Do not accidentally treat every
    // opaque parent as same-origin when this page itself was opened from a
    // file/custom URL; the one supported opaque case is checked explicitly.
    if (origin !== "null" && origin === window.location.origin) return true;
    if (isLoopbackOrigin(origin)) return true;
    if (origin === "null" && window.location.protocol === "echo-app:") return true;
    return origin === "https://echo-age.com" ||
      origin === "https://ai.echo-age.com" ||
      origin === "https://os.echo-age.com" ||
      origin === "https://api.echo-age.com";
  }

  function trustedQuoteBase(value) {
    if (typeof value !== "string" || !value.trim()) return "";
    var origin = parsedOrigin(value.trim());
    if (!origin) return null;
    if (origin === window.location.origin || isLoopbackOrigin(origin)) return origin;
    if (origin === "https://quotes.echo-age.com" || origin === "https://api.echo-age.com") {
      return origin;
    }
    return null;
  }

  function requestQuoteConfig(reason) {
    if (window.parent === window) return;
    window.parent.postMessage(
      { type: "echo:quote-config-request", reason: reason || "initial" },
      parentOrigin && parentOrigin !== "null" ? parentOrigin : "*"
    );
  }

  function applyQuoteConfig(message, origin) {
    var base = trustedQuoteBase(message.quoteBaseUrl);
    if (base == null) return;
    var nextToken = typeof message.bearer === "string" ? message.bearer.trim() : "";
    var nextUrl = (base || "") + QUOTE_PATH;
    var changed = nextUrl !== QUOTE_URL || nextToken !== bearerToken;
    parentOrigin = origin;
    bearerToken = nextToken;
    QUOTE_URL = nextUrl;
    if (bootTimer) clearTimeout(bootTimer);
    bootTimer = null;
    if (!changed || !booted) return;
    closeStream(false);
    stopFallbackPolling();
    loadStructure();
    pollSnapshot();
    openStream();
  }

  function authenticatedOptions(options) {
    var result = options || {};
    var headers = Object.assign({}, result.headers || {});
    if (bearerToken) headers.Authorization = "Bearer " + bearerToken;
    result.headers = headers;
    result.credentials = "omit";
    return result;
  }

  window.addEventListener("message", function (event) {
    if (event.source !== window.parent || !isTrustedParentOrigin(event.origin)) return;
    var message = event.data;
    if (!message || message.type !== "echo:quote-config" || message.version !== 1) return;
    applyQuoteConfig(message, event.origin);
    boot();
  });

  function quoteIsStale(quote) {
    if (!quote || quote.stale) return true;
    var received = Date.parse(quote.received_at || "");
    return !isNaN(received) && Date.now() - received > 15000;
  }

  function quoteFor(code) {
    return latestQuotes[baseCode(code)] || null;
  }

  function localTime(value) {
    var date = value ? new Date(value) : new Date();
    if (isNaN(date.getTime())) date = new Date();
    try {
      return date.toLocaleTimeString("zh-CN", {
        hour12: false,
        hour: "2-digit",
        minute: "2-digit",
        second: "2-digit",
        timeZone: "Asia/Shanghai"
      });
    } catch (_err) {
      return date.toLocaleTimeString();
    }
  }

  function sparkSvg() {
    return '<svg class="spark" width="100%" height="26" preserveAspectRatio="none" viewBox="0 0 100 26"></svg>';
  }

  function renderSpark(element, values) {
    if (!element || !values || values.length < 2) return;
    var list = values.map(Number).filter(function (value) { return !isNaN(value); });
    if (list.length < 2) return;
    var min = Math.min.apply(null, list);
    var max = Math.max.apply(null, list);
    var range = (max - min) || 1;
    var points = list.map(function (value, index) {
      var x = (index / (list.length - 1)) * 100;
      var y = 25 - ((value - min) / range) * 22 - 1.5;
      return x.toFixed(1) + "," + y.toFixed(1);
    }).join(" ");
    var color = list[list.length - 1] >= list[0] ? "var(--up)" : "var(--down)";
    element.innerHTML = '<polyline points="' + points + '" fill="none" stroke="' + color + '" stroke-width="1.5"/>';
  }

  function indexCard(item) {
    var pct = item.change_pct;
    var color = cls(pct);
    return '<div class="idx"><div class="nm">' + esc(item.name || item.symbol || "") + '</div>' +
      '<div class="row"><span class="px ' + color + '">' + fmt(item.price) + '</span>' +
      '<span class="pc ' + color + '">' + sign(pct) + fmt(pct) + '%</span>' +
      '<span class="pc flat">' + sign(item.change) + fmt(item.change) + '</span></div>' +
      sparkSvg() + '</div>';
  }

  function positionRows(list) {
    if (!list || !list.length) return { html: '<div class="empty">暂无持仓</div>', alerts: [] };
    var alerts = [];
    var html = '<table><thead><tr><th>股票</th><th>现价</th><th>盈亏</th><th>持仓</th><th>可卖</th><th>成本</th><th>浮盈额</th><th>浮盈%</th></tr></thead><tbody>';
    list.forEach(function (position) {
      var quote = quoteFor(position.code);
      var stale = quoteIsStale(quote);
      var current = quote && quote.price != null ? Number(quote.price) : Number(position.currentPrice);
      var cost = Number(position.cost);
      var pct = cost > 0 && !isNaN(current) ? ((current / cost) - 1) * 100 : Number(position.floatingProfitAndLossPercentage);
      var warn = !stale && !isNaN(pct) && Math.abs(pct) >= TH;
      if (warn) alerts.push((position.codeName || position.code) + "(" + position.code + ") " + sign(pct) + pct.toFixed(2) + "%");
      html += '<tr class="' + (stale ? 'stale ' : '') + (warn ? 'warn' : '') + '">' +
        '<td class="nm-c"><span class="tick">' + (stale ? '⌛' : (warn ? '⚠' : '')) + '</span>' + esc(position.codeName || "") + '<span class="code">' + esc(position.code) + '</span></td>' +
        '<td class="' + cls(pct) + '">' + fmt(current) + '</td>' +
        '<td class="' + cls(pct) + '">' + sign(pct) + fmt(pct) + '%</td>' +
        '<td>' + esc(position.position) + '</td><td>' + esc(position.sellable) + '</td>' +
        '<td>' + fmt(cost) + '</td>' +
        '<td class="' + cls(position.floatingProfitAndLoss) + '">' + sign(position.floatingProfitAndLoss) + fmt(position.floatingProfitAndLoss) + '</td>' +
        '<td class="' + cls(position.floatingProfitAndLossPercentage) + '">' + sign(position.floatingProfitAndLossPercentage) + fmt(position.floatingProfitAndLossPercentage) + '</td></tr>';
    });
    return { html: html + '</tbody></table>', alerts: alerts };
  }

  function watchlistRows(list) {
    if (!list || !list.length) return { html: '<div class="empty">自选为空 — 在平台原版页面把股票加入自选</div>', alerts: [] };
    var alerts = [];
    var html = '<table><thead><tr><th>股票</th><th>现价</th><th>涨跌</th><th>最高</th><th>最低</th><th>今开</th><th>成交量</th><th>成交额</th></tr></thead><tbody>';
    list.forEach(function (item) {
      var quote = quoteFor(item.stockCode);
      var stale = quoteIsStale(quote);
      var pct = quote && quote.change_pct != null ? Number(quote.change_pct) : Number(item.stockIncrease);
      var price = quote && quote.price != null ? Number(quote.price) : Number(item.stockPrice);
      var warn = !stale && !isNaN(pct) && Math.abs(pct) >= TH;
      if (warn) alerts.push((item.stockName || item.stockCode) + "(" + item.stockCode + ") " + sign(pct) + pct.toFixed(2) + "%");
      var amount = quote && quote.amount != null ? quote.amount : (item.amount ? (Number(item.amount) >= 1e8 ? (item.amount / 1e8).toFixed(2) + "亿" : (item.amount / 1e4).toFixed(0) + "万") : "--");
      html += '<tr class="' + (stale ? 'stale ' : '') + (warn ? 'warn' : '') + '">' +
        '<td class="nm-c"><span class="tick">' + (stale ? '⌛' : (warn ? '⚠' : '')) + '</span>' + esc(item.stockName || "") + '<span class="code">' + esc(item.stockCode) + '</span></td>' +
        '<td class="' + cls(pct) + '">' + fmt(price) + '</td>' +
        '<td class="' + cls(pct) + '">' + sign(pct) + fmt(pct) + '%</td>' +
        '<td>' + fmt(quote && quote.high != null ? quote.high : item.high) + '</td>' +
        '<td>' + fmt(quote && quote.low != null ? quote.low : item.low) + '</td>' +
        '<td>' + fmt(quote && quote.open != null ? quote.open : item.open) + '</td>' +
        '<td>' + esc((quote && quote.volume) || item.stockVolStr || "--") + '</td>' +
        '<td>' + esc(amount) + '</td></tr>';
    });
    return { html: html + '</tbody></table>', alerts: alerts };
  }

  function render(data) {
    data = data || lastData;
    var badge = $("statusBadge");
    var status = data.status || "未开盘";
    badge.textContent = status;
    badge.className = "badge " + (/交易中|盘中/.test(status) ? "open" : "closed");

    var indices = data.indices || [];
    $("indices").innerHTML = indices.map(indexCard).join("") || '<div class="empty">无指数数据</div>';
    var sparks = $("indices").querySelectorAll(".spark");
    indices.forEach(function (item, index) { renderSpark(sparks[index], item.spark); });

    var breadth = data.breadth || {};
    $("breadth").innerHTML =
      '<span>上涨 <b class="up">' + (breadth.up || 0) + '</b></span>' +
      '<span>下跌 <b class="down">' + (breadth.down || 0) + '</b></span>' +
      '<span>平盘 <b class="flat">' + (breadth.unchanged || 0) + '</b></span>' +
      '<span>涨停 <b class="up">' + (breadth.stop || 0) + '</b></span>';

    var positions = positionRows(data.positions || []);
    var watchlist = watchlistRows(data.watchlist || []);
    $("posCnt").textContent = (data.positions || []).length;
    $("wlCnt").textContent = (data.watchlist || []).length;
    $("positions").innerHTML = positions.html;
    $("watchlist").innerHTML = watchlist.html;

    var alerts = positions.alerts.concat(watchlist.alerts);
    var banner = $("alertBanner");
    if (alerts.length) {
      banner.classList.add("show");
      banner.textContent = "⚠ 提醒触发(" + TH + "%)：" + alerts.join("；");
    } else {
      banner.classList.remove("show");
      banner.textContent = "";
    }
    $("updated").textContent = "更新 " + localTime(data.fetched_at);
  }

  function scheduleRender() {
    if (renderTimer) return;
    renderTimer = setTimeout(function () {
      renderTimer = null;
      render(lastData);
    }, RENDER_BATCH_MS);
  }

  function collectCodes(data) {
    var seen = {};
    var codes = [];
    function add(value) {
      var code = baseCode(value);
      if (!/^\d{6}$/.test(code) || seen[code] || codes.length >= MAX_CODES) return;
      seen[code] = true;
      codes.push(code);
    }
    (data.positions || []).forEach(function (item) { add(item.code); });
    (data.watchlist || []).forEach(function (item) { add(item.stockCode); });
    return codes.sort();
  }

  function updateFeed(status) {
    status = status || {};
    if (status.source_labels) sourceLabels = status.source_labels;
    var source = status.active_source || status.source || "";
    var label = sourceLabels[source] || source || "行情中心";
    var age = status.age_ms;
    if (age == null && status.received_at) {
      var received = Date.parse(status.received_at);
      if (!isNaN(received)) age = Math.max(0, Date.now() - received);
    }
    var ageText = age == null ? "" : (age < 1000 ? Math.round(age) + "ms" : (age / 1000).toFixed(1) + "s");
    var element = $("feedMode");
    if (status.state === "stale" || (age != null && age > 15000)) {
      element.textContent = "行情已暂停" + (ageText ? " · " + ageText + " 未更新" : "");
      element.className = "stale";
    } else if (status.state === "fallback" || status.degraded || (source && source !== "platform_ws")) {
      element.textContent = label + (ageText ? " · " + ageText : "");
      element.className = "fallback";
    } else if (status.state === "idle") {
      element.textContent = "等待股票订阅";
      element.className = "";
    } else {
      element.textContent = label + (ageText ? " · " + ageText : "");
      element.className = "live";
    }
  }

  function applyQuoteEnvelope(payload) {
    var quotes = payload && Array.isArray(payload.quotes) ? payload.quotes : [];
    quotes.forEach(function (quote) {
      if (!quote || typeof quote !== "object") return;
      var code = baseCode(quote.code);
      var seq = Number(quote.seq || payload.seq || 0);
      if (!code || (latestSeq[code] != null && seq <= latestSeq[code])) return;
      latestSeq[code] = seq;
      latestQuotes[code] = quote;
    });
    updateFeed(payload || {});
    scheduleRender();
  }

  function stopFallbackPolling() {
    if (fallbackTimer) clearInterval(fallbackTimer);
    fallbackTimer = null;
  }

  function pollSnapshot() {
    if (!desiredCodes.length || document.hidden) return;
    fetch(
      QUOTE_URL + "/snapshot?codes=" + encodeURIComponent(desiredCodes.join(",")),
      authenticatedOptions({ cache: "no-store" })
    )
      .then(function (response) {
        if (!response.ok) {
          var error = new Error("行情快照请求失败: " + response.status);
          error.status = response.status;
          throw error;
        }
        return response.json();
      })
      .then(function (payload) {
        applyQuoteEnvelope(payload);
        if (payload && payload.status) updateFeed(payload.status);
      })
      .catch(function (error) {
        if (error && error.status === 401) {
          $("feedMode").textContent = "登录状态已过期 · 等待重新认证";
          $("feedMode").className = "stale";
          stopFallbackPolling();
          requestQuoteConfig("unauthorized");
        }
      });
  }

  function startFallbackPolling() {
    if (fallbackTimer || !desiredCodes.length) return;
    pollSnapshot();
    fallbackTimer = setInterval(pollSnapshot, 4000);
  }

  function parseSseBlock(block) {
    var eventName = "message";
    var data = [];
    var retry = null;
    String(block || "").split(/\r\n|\r|\n/).forEach(function (line) {
      if (!line || line.charAt(0) === ":") return;
      var colon = line.indexOf(":");
      var field = colon < 0 ? line : line.slice(0, colon);
      var value = colon < 0 ? "" : line.slice(colon + 1);
      if (value.charAt(0) === " ") value = value.slice(1);
      if (field === "event" && value) eventName = value;
      else if (field === "data") data.push(value);
      else if (field === "retry" && /^\d+$/.test(value)) retry = Number(value);
    });
    return { event: eventName, data: data.join("\n"), retry: retry };
  }

  function dispatchSsePacket(packet) {
    if (packet.retry != null) {
      serverRetryMs = Math.max(500, Math.min(30000, packet.retry));
    }
    if (!packet.data) return false;
    try {
      var payload = JSON.parse(packet.data);
      if (packet.event === "snapshot" || packet.event === "quote") {
        applyQuoteEnvelope(payload);
      } else if (packet.event === "status") {
        updateFeed(payload);
      } else if (packet.event === "reauth") {
        $("feedMode").textContent = "正在刷新行情授权…";
        $("feedMode").className = "fallback";
        requestQuoteConfig("reauth");
        return true;
      }
    } catch (_err) {}
    return false;
  }

  async function consumeSse(response) {
    if (!response.body || typeof response.body.getReader !== "function") {
      throw new Error("浏览器不支持流式 fetch");
    }
    var reader = response.body.getReader();
    var decoder = new TextDecoder("utf-8");
    var buffer = "";
    while (true) {
      var chunk = await reader.read();
      if (chunk.done) return "eof";
      buffer += decoder.decode(chunk.value, { stream: true });
      if (buffer.length > 1024 * 1024) {
        await reader.cancel();
        throw new Error("行情流缓冲区超过安全上限");
      }
      while (true) {
        var separator = buffer.match(/(?:\r\n|\r|\n){2}/);
        if (!separator || separator.index == null) break;
        var block = buffer.slice(0, separator.index);
        buffer = buffer.slice(separator.index + separator[0].length);
        if (dispatchSsePacket(parseSseBlock(block))) {
          await reader.cancel();
          return "reauth";
        }
      }
    }
  }

  function closeStream(clearKey) {
    streamRunId += 1;
    if (streamAbort) streamAbort.abort();
    streamAbort = null;
    if (reconnectTimer) clearTimeout(reconnectTimer);
    reconnectTimer = null;
    if (clearKey) streamKey = "";
  }

  function scheduleReconnect(reason, status) {
    if (document.hidden || !desiredCodes.length || reconnectTimer) return;
    if (status === 401) return;
    var delay;
    if (reason === "reauth") delay = 250;
    else if (status === 429) delay = 10000;
    else {
      delay = Math.min(30000, serverRetryMs * Math.pow(2, reconnectAttempt));
      reconnectAttempt = Math.min(5, reconnectAttempt + 1);
    }
    reconnectTimer = setTimeout(function () {
      reconnectTimer = null;
      openStream();
    }, delay);
  }

  function openStream() {
    if (document.hidden || !desiredCodes.length) return;
    var key = desiredCodes.join(",");
    if (streamAbort && streamKey === key) return;
    closeStream(false);
    streamKey = key;
    if (!window.AbortController || !window.TextDecoder) {
      startFallbackPolling();
      return;
    }

    var controller = new AbortController();
    var runId = ++streamRunId;
    streamAbort = controller;
    var url = QUOTE_URL + "/stream?codes=" + encodeURIComponent(key);

    (async function () {
      var reason = "error";
      var status = 0;
      try {
        var response = await fetch(url, authenticatedOptions({
          cache: "no-store",
          headers: { Accept: "text/event-stream", "Cache-Control": "no-cache" },
          signal: controller.signal
        }));
        status = response.status;
        if (!response.ok) {
          var httpError = new Error("行情流请求失败: " + response.status);
          httpError.status = response.status;
          throw httpError;
        }
        var contentType = (response.headers.get("content-type") || "").toLowerCase();
        if (contentType.indexOf("text/event-stream") < 0) {
          throw new Error("行情流响应类型错误");
        }
        reconnectAttempt = 0;
        stopFallbackPolling();
        $("feedMode").textContent = "行情中心已连接 · 等待数据";
        $("feedMode").className = "live";
        reason = await consumeSse(response);
      } catch (error) {
        if (controller.signal.aborted || runId !== streamRunId) return;
        status = Number(error && error.status) || status;
        if (status === 401) {
          $("feedMode").textContent = "登录状态已过期 · 等待重新认证";
          $("feedMode").className = "stale";
          requestQuoteConfig("unauthorized");
        } else if (status === 429) {
          $("feedMode").textContent = "实时连接较多 · 稍后重试";
          $("feedMode").className = "fallback";
        } else {
          $("feedMode").textContent = "实时流断开 · 快照兜底";
          $("feedMode").className = "fallback";
        }
      } finally {
        if (runId !== streamRunId) return;
        streamAbort = null;
        if (status === 401) {
          stopFallbackPolling();
          return;
        }
        startFallbackPolling();
        scheduleReconnect(reason, status);
      }
    })();
  }

  function syncStream(data) {
    var next = collectCodes(data);
    var nextKey = next.join(",");
    desiredCodes = next;
    if (!next.length) {
      closeStream(true);
      stopFallbackPolling();
      updateFeed({ state: "idle" });
      return;
    }
    if (streamKey !== nextKey || !streamAbort) openStream();
  }

  function loadStructure() {
    fetch(STRUCTURE_URL, authenticatedOptions({ cache: "no-store" }))
      .then(function (response) {
        if (!response.ok) {
          var error = new Error("盯盘结构请求失败: " + response.status);
          error.status = response.status;
          throw error;
        }
        return response.json();
      })
      .then(function (data) {
        if (!data.available) {
          $("positions").innerHTML = '<div class="err">盯盘暂不可用：' + esc(data.error || data.message || "请检查平台连接") + '</div>';
          $("watchlist").innerHTML = "";
          $("statusBadge").textContent = "未连接";
          updateFeed({ state: "stale" });
          return;
        }
        lastData = data;
        render(lastData);
        syncStream(lastData);
      })
      .catch(function (error) {
        if (error && error.status === 401) requestQuoteConfig("unauthorized");
        $("positions").innerHTML = '<div class="err">加载失败：' + esc(error) + '</div>';
        updateFeed({ state: "stale" });
      });
  }

  $("alertTh").addEventListener("change", function (event) {
    TH = Number(event.target.value);
    render(lastData);
  });

  function boot() {
    if (booted) return;
    booted = true;
    if (bootTimer) clearTimeout(bootTimer);
    bootTimer = null;
    loadStructure();
    structureTimer = setInterval(loadStructure, 10000);
  }

  document.addEventListener("visibilitychange", function () {
    if (document.hidden) {
      if (structureTimer) clearInterval(structureTimer);
      structureTimer = null;
      closeStream(false);
      stopFallbackPolling();
    } else {
      if (!booted) {
        requestQuoteConfig("visible");
        boot();
        return;
      }
      requestQuoteConfig("visible");
      loadStructure();
      if (!structureTimer) structureTimer = setInterval(loadStructure, 10000);
      openStream();
    }
  });
  window.addEventListener("pagehide", function () {
    if (bootTimer) clearTimeout(bootTimer);
    bootTimer = null;
    if (structureTimer) clearInterval(structureTimer);
    structureTimer = null;
    closeStream(true);
    stopFallbackPolling();
    bearerToken = "";
    parentOrigin = "";
  });
  window.addEventListener("pageshow", function (event) {
    if (!event.persisted || !booted) return;
    if (!structureTimer) structureTimer = setInterval(loadStructure, 10000);
    if (window.parent === window) {
      loadStructure();
      openStream();
      return;
    }
    requestQuoteConfig("pageshow");
    bootTimer = setTimeout(function () {
      bootTimer = null;
      loadStructure();
      openStream();
    }, 750);
  });

  if (window.parent === window) {
    boot();
  } else {
    requestQuoteConfig("initial");
    bootTimer = setTimeout(boot, 750);
  }
})();
