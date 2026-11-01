(function () {
  "use strict";

  var stage = document.getElementById("monitor-stage");
  var dashboard = document.getElementById("dashboard");
  var lastData = null;
  var lastHistoryTime = 0;
  var lastRouterSampleTime = 0;
  var history = { router: [], devices: [] };
  var historyLimit = 60;
  var chartWindowSeconds = 120;
  var chartMaximums = {};
  var clockEpoch = Date.now();
  var clockReceivedAt = Date.now();
  var slots = [
    { key: "ubcloud", label: "UBCLOUD", color: "#6ba8ff", detail: "Intel 14700K + NVIDIA 4070Super", aliases: ["ubcloud", "ub", "ubuntu"], third: "gpu", thirdLabel: "GPU" },
    { key: "maccloud", label: "MACCLOUD", color: "#b58cff", detail: "Apple MacBook Pro M1 Pro 2021", aliases: ["maccloud", "mac", "macbook"], third: "temperature", thirdLabel: "温度" },
    { key: "syncloud", label: "SYNCLOUD", color: "#65d89b", detail: "Synology DS423+", aliases: ["syncloud", "nas", "synology"], third: "temperature", thirdLabel: "温度" },
    { key: "pvecloud", label: "PVECLOUD", color: "#f0b75a", detail: "Beelink EQ13 mini Intel N95", aliases: ["pvecloud", "pve", "proxmox"], third: "temperature", thirdLabel: "温度" }
  ];

  function escapeHtml(value) {
    return String(value == null ? "" : value).replace(/[&<>"']/g, function (character) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", "\"": "&quot;", "'": "&#39;" }[character];
    });
  }

  function clamp(value) { return Math.max(0, Math.min(100, Number(value) || 0)); }
  function percent(value) { return value == null ? "--" : Math.round(Number(value)) + "%"; }
  function temperature(value) { return value == null ? "--" : Math.round(Number(value)) + "°C"; }
  function pad(value) { return value < 10 ? "0" + value : String(value); }

  function fitStage() {
    var scale = Math.min(window.innerWidth / 1080, window.innerHeight / 1920);
    var transform = "translate(-50%, -50%) scale(" + scale + ")";
    stage.style.webkitTransform = transform;
    stage.style.transform = transform;
  }

  function findSystem(slot, systems, used) {
    var exact = systems.find(function (system) {
      return !used[system.id] && system.name.toLowerCase() === slot.label.toLowerCase();
    });
    if (exact) return exact;
    return systems.find(function (system) {
      var name = system.name.toLowerCase();
      return !used[system.id] && slot.aliases.some(function (alias) { return name.indexOf(alias) !== -1; });
    });
  }

  function metricColor(value, kind) {
    if (value == null) return "#5d5d5d";
    value = Number(value) || 0;
    if (kind === "temperature") {
      if (value < 55) return "#65d89b";
      if (value < 70) return "#f0b75a";
      return "#ee6f78";
    }
    if (value < 60) return "#65d89b";
    if (value < 80) return "#f0b75a";
    return "#ee6f78";
  }

  function metric(label, value, rawValue, kind) {
    var width = rawValue == null ? 0 : clamp(rawValue);
    var color = metricColor(rawValue, kind);
    return '<div class="metric-line"><div class="metric-caption"><span>' + escapeHtml(label) + '</span>' +
      '<strong>' + escapeHtml(value) + '</strong></div><div class="metric-track">' +
      '<i class="metric-fill" style="width:' + width + '%;background-color:' + color + '"></i></div></div>';
  }

  function loadAverage(system) {
    if (!system || !system.load || system.load.length === 0 || system.load[0] == null) return null;
    var value = Number(system.load[0]);
    return isFinite(value) ? value : null;
  }

  function loadPercent(system, value) {
    if (value == null) return null;
    var threads = Number(system && system.threads) || 0;
    return threads > 0 ? value / threads * 100 : value * 10;
  }

  function deviceCard(slot, system, position) {
    var online = system && system.status === "up";
    var thirdValue = system ? system[slot.third] : null;
    var thirdText = slot.third === "temperature" ? temperature(thirdValue) : percent(thirdValue);
    var thirdRing = slot.third === "temperature" ? clamp(thirdValue) : thirdValue;
    var load = loadAverage(system);
    return '<article class="device-panel device-' + position + '">' +
      '<header class="device-header"><div><div class="device-label">' + escapeHtml(slot.label) + '</div>' +
      '<div class="device-detail">' + escapeHtml(slot.detail) + '</div></div>' +
      '<div class="device-state ' + (online ? "online" : "offline") + '"><i></i>' + (online ? "在线" : "离线") + '</div></header>' +
      '<div class="metric-list">' +
        metric("CPU", percent(system && system.cpu), system && system.cpu, "percent") +
        metric("内存", percent(system && system.memory), system && system.memory, "percent") +
        metric(slot.thirdLabel, thirdText, thirdRing, slot.third) +
        metric("系统负载", load == null ? "--" : load.toFixed(2), loadPercent(system, load), "percent") +
      '</div></article>';
  }

  function currentClock() {
    var beijing = new Date(clockEpoch + (Date.now() - clockReceivedAt) + 8 * 3600000);
    return pad(beijing.getUTCHours()) + ":" + pad(beijing.getUTCMinutes()) + ":" + pad(beijing.getUTCSeconds());
  }

  function weatherLine(weather) {
    if (!weather || (weather.status !== "up" && weather.status !== "stale")) return "天气数据暂不可用";
    return Math.round(Number(weather.minimum)) + "–" + Math.round(Number(weather.maximum)) + "°C  " +
      escapeHtml(weather.condition || "天气未知") + "  降雨" + Math.round(Number(weather.precipitation_probability) || 0) + "%";
  }

  function timePanel(data) {
    var calendar = data.calendar || {};
    var dateLine = calendar.date ? calendar.date + "  " + calendar.weekday + "  " + calendar.lunar : "正在获取日期";
    return '<header class="time-panel"><strong id="beijing-clock">' + currentClock() + '</strong>' +
      '<span id="beijing-date">' + escapeHtml(dateLine) + '</span>' +
      '<span id="beijing-weather">' + weatherLine(data.weather) + '</span></header>';
  }

  function tickClock() {
    var clock = document.getElementById("beijing-clock");
    if (clock) clock.textContent = currentClock();
  }

  function rememberNetwork(data, matched) {
    var timestamp = Number(data.server_time) || Date.now() / 1000;
    var wan = data.wan || {};
    var routerTime = Number(wan.sampled_at);
    if (wan.status === "disabled") {
      history.router = [];
      lastRouterSampleTime = 0;
    } else if (isFinite(routerTime) && routerTime > lastRouterSampleTime) {
      lastRouterSampleTime = routerTime;
      history.router.push({
        time: routerTime,
        down: wan.status === "up" ? trafficValue(wan.download_bps) : null,
        up: wan.status === "up" ? trafficValue(wan.upload_bps) : null,
        interval: Number(wan.poll_seconds) || 2
      });
      if (history.router.length > historyLimit) history.router.shift();
    }
    if (!data.server_time || timestamp === lastHistoryTime) return;
    lastHistoryTime = timestamp;
    var devices = {};
    devices.time = timestamp;
    slots.forEach(function (slot, index) {
      var system = matched[index] || {};
      devices[slot.key + "_down"] = Number(system.network_down_bps) || 0;
      devices[slot.key + "_up"] = Number(system.network_up_bps) || 0;
    });
    history.devices.push(devices);
    if (history.devices.length > historyLimit) history.devices.shift();
  }

  function trafficValue(value) {
    if (value == null || !isFinite(Number(value)) || Number(value) < 0) return null;
    return Number(value);
  }

  function terminalPanel(terminal) {
    terminal = terminal || {};
    var lines = terminal.lines || [];
    var status = terminal.status === "up" ? (lines.length ? "实时" : "等待输出") : terminal.status === "error" ? "读取失败" : "未启用";
    var output;
    if (lines.length) output = lines.join("\n");
    else if (terminal.error) output = terminal.error;
    else if (terminal.status === "up") output = "SSH 已连接，日志命令执行成功，但当前没有输出。\n请确认训练程序正在写入该日志文件或 systemd journal。";
    else output = "Ubuntu 输出监控尚未启用，请在设置页启用并保存。";
    return '<section class="terminal-panel"><header><strong>Ubuntu 训练终端</strong><span class="terminal-state ' + escapeHtml(terminal.status || "disabled") + '"><i></i>' + status + '</span></header>' +
      '<pre id="ubuntu-terminal-output">' + escapeHtml(output) + '</pre></section>';
  }

  function lineLegend(color, label, dashed) {
    return '<span><i class="line-key ' + (dashed ? "dashed" : "solid") + '" style="border-color:' + color + '"></i>' + escapeHtml(label) + '</span>';
  }

  function chartCard(id, title, legend) {
    return '<section class="chart-card"><div class="chart-title"><strong>' + escapeHtml(title) + '</strong></div>' +
      '<canvas id="' + id + '" class="throughput-chart" width="454" height="240"></canvas>' +
      '<div class="chart-legend">' + legend + '</div></section>';
  }

  function networkCharts() {
    var routerLegend = '<div class="legend-row legend-directions">' + lineLegend("#7fb8ff", "下载", false) + lineLegend("#7fb8ff", "上传", true) + '</div>';
    var deviceLegend = '<div class="legend-row legend-devices">' + slots.map(function (slot) {
      return lineLegend(slot.color, slot.label, false);
    }).join("") + '</div>';
    return '<section class="network-panel"><div class="network-charts">' +
      chartCard("router-chart", "路由器曲线图", routerLegend) +
      chartCard("devices-chart", "设备流量吞吐", deviceLegend) +
      '</div></section>';
  }

  function niceMaximum(value) {
    if (!value || value < 1000) return 1000;
    var power = Math.pow(10, Math.floor(Math.log(value) / Math.LN10));
    var scaled = value / power;
    var nice = scaled <= 1 ? 1 : scaled <= 2 ? 2 : scaled <= 5 ? 5 : 10;
    return nice * power;
  }

  function axisNumber(value) {
    if (value >= 1000000000) return (value / 1000000000).toFixed(value >= 10000000000 ? 0 : 1).replace(/\.0$/, "") + "G";
    if (value >= 1000000) return (value / 1000000).toFixed(1).replace(/\.0$/, "") + "M";
    if (value >= 1000) return (value / 1000).toFixed(value >= 10000 ? 0 : 1).replace(/\.0$/, "") + "K";
    return String(Math.round(value));
  }

  function drawChart(canvas, points, definitions) {
    if (!canvas || !canvas.getContext) return;
    var context = canvas.getContext("2d");
    var width = canvas.width;
    var height = canvas.height;
    var left = 62;
    var right = 14;
    var top = 16;
    var bottom = 18;
    var plotWidth = width - left - right;
    var plotHeight = height - top - bottom;
    var endTime = Number(lastData && lastData.server_time) || Date.now() / 1000;
    var startTime = endTime - chartWindowSeconds;
    points = points.filter(function (point) { return point.time >= startTime && point.time <= endTime; });
    var highest = points.reduce(function (highest, point) {
      definitions.forEach(function (definition) {
        highest = Math.max(highest, Number(point[definition.field]) || 0);
      });
      return highest;
    }, 0);
    // Start at 0–50 Mbps; retain an expanded scale for this page session.
    var maximum = chartMaximums[canvas.id] || 50000000;
    if (highest > maximum) maximum = niceMaximum(highest);
    chartMaximums[canvas.id] = maximum;

    context.clearRect(0, 0, width, height);
    context.font = "17px sans-serif";
    context.textAlign = "right";
    context.textBaseline = "middle";
    for (var line = 0; line <= 4; line += 1) {
      var y = top + plotHeight * line / 4;
      context.beginPath();
      context.strokeStyle = "#3b3b3b";
      context.lineWidth = 1;
      context.moveTo(left, y);
      context.lineTo(width - right, y);
      context.stroke();
      context.fillStyle = "#888";
      context.fillText(axisNumber(maximum * (4 - line) / 4), left - 9, y);
    }

    function segment(vertices, color) {
      if (!vertices.length) return;
      if (vertices.length === 1) {
        context.beginPath();
        context.fillStyle = color;
        context.arc(vertices[0].x, vertices[0].y, 2, 0, Math.PI * 2);
        context.fill();
        return;
      }
      var slopes = [], tangents = [];
      for (var i = 0; i < vertices.length - 1; i += 1) {
        slopes.push((vertices[i + 1].y - vertices[i].y) / (vertices[i + 1].x - vertices[i].x));
      }
      tangents[0] = slopes[0];
      tangents[vertices.length - 1] = slopes[slopes.length - 1];
      for (var j = 1; j < vertices.length - 1; j += 1) {
        tangents[j] = slopes[j - 1] * slopes[j] <= 0 ? 0 :
          2 / (1 / slopes[j - 1] + 1 / slopes[j]);
      }
      // Limit the Hermite tangents so curves cannot overshoot the actual samples.
      slopes.forEach(function (slope, index) {
        if (slope === 0) { tangents[index] = 0; tangents[index + 1] = 0; return; }
        var a = tangents[index] / slope, b = tangents[index + 1] / slope;
        var length = Math.sqrt(a * a + b * b);
        if (length > 3) {
          tangents[index] = 3 * a * slope / length;
          tangents[index + 1] = 3 * b * slope / length;
        }
      });
      context.beginPath();
      context.moveTo(vertices[0].x, vertices[0].y);
      for (var k = 0; k < vertices.length - 1; k += 1) {
        var from = vertices[k], to = vertices[k + 1], dx = (to.x - from.x) / 3;
        context.bezierCurveTo(from.x + dx, from.y + tangents[k] * dx,
          to.x - dx, to.y - tangents[k + 1] * dx, to.x, to.y);
      }
      context.stroke();
    }

    function series(definition) {
      context.strokeStyle = definition.color;
      context.lineWidth = 3;
      context.setLineDash(definition.dashed ? [10, 8] : []);
      context.lineJoin = "round";
      context.lineCap = "round";
      var vertices = [], previousTime = null;
      points.forEach(function (point) {
        var value = trafficValue(point[definition.field]);
        var gap = previousTime != null && point.time - previousTime > Math.max(10, (point.interval || 2) * 3);
        if (value == null || gap) {
          segment(vertices, definition.color);
          vertices = [];
        }
        if (value != null) vertices.push({
          x: left + plotWidth * (point.time - startTime) / chartWindowSeconds,
          y: top + plotHeight * (1 - Math.min(value, maximum) / maximum)
        });
        previousTime = point.time;
      });
      segment(vertices, definition.color);
    }

    context.save();
    context.beginPath();
    context.rect(left, top, plotWidth, plotHeight);
    context.clip();
    definitions.forEach(series);
    context.restore();
    context.setLineDash([]);
  }

  function render(data, connectionState) {
    lastData = data;
    var rotation = Number(data.display && data.display.rotation) === 180 ? 180 : 0;
    dashboard.setAttribute("data-rotation", String(rotation));
    if (data.server_time) {
      clockEpoch = Number(data.server_time) * 1000;
      clockReceivedAt = Date.now();
    }
    var systems = (data.beszel && data.beszel.systems) || [];
    var used = {};
    var matched = slots.map(function (slot) {
      var system = findSystem(slot, systems, used);
      if (system) used[system.id] = true;
      return system || null;
    });
    rememberNetwork(data, matched);
    dashboard.innerHTML = timePanel(data) + deviceCard(slots[0], matched[0], 1) + deviceCard(slots[1], matched[1], 2) +
      deviceCard(slots[2], matched[2], 3) + deviceCard(slots[3], matched[3], 4) +
      terminalPanel(data.terminal) + networkCharts();
    var output = document.getElementById("ubuntu-terminal-output");
    if (output) output.scrollTop = output.scrollHeight;
    drawChart(document.getElementById("router-chart"), history.router, [
      { field: "down", color: "#7fb8ff", dashed: false },
      { field: "up", color: "#7fb8ff", dashed: true }
    ]);
    var deviceDefinitions = [];
    slots.forEach(function (slot) {
      deviceDefinitions.push({ field: slot.key + "_down", color: slot.color, dashed: false });
      deviceDefinitions.push({ field: slot.key + "_up", color: slot.color, dashed: true });
    });
    drawChart(document.getElementById("devices-chart"), history.devices, deviceDefinitions);
  }

  function connect() {
    var events = new EventSource("/api/events");
    events.onopen = function () { if (lastData) render(lastData, "live"); };
    events.onmessage = function (event) {
      try { render(JSON.parse(event.data), "live"); } catch (error) { console.error(error); }
    };
    events.onerror = function () { if (lastData) render(lastData, "waiting"); };
  }

  fitStage();
  render({ beszel: { systems: [] } }, "waiting");
  connect();
  window.setInterval(tickClock, 250);
  window.addEventListener("resize", fitStage);
})();
