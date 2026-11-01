const assert = require("node:assert/strict");
const { test } = require("node:test");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const source = fs.readFileSync(path.join(__dirname, "../app/static/app.js"), "utf8");

function dashboard() {
  function canvas(id) {
    const context = {
      labels: [], paths: [], dots: [], dash: [], current: [],
      clearRect() { this.labels = []; this.paths = []; this.dots = []; },
      beginPath() { this.current = []; this.dot = null; },
      moveTo(x, y) { this.current.push({ type: "move", x, y }); },
      lineTo(x, y) { this.current.push({ type: "line", x, y }); },
      bezierCurveTo(...values) { this.current.push({ type: "curve", values }); },
      stroke() {
        if (this.lineWidth === 3) this.paths.push({ dash: this.dash.slice(), vertices: this.current.slice() });
      },
      fillText(text) { this.labels.push(text); },
      setLineDash(dash) { this.dash = dash; },
      arc(x, y) { this.dot = { x, y }; },
      fill() { if (this.dot) this.dots.push({ ...this.dot, dash: this.dash.slice() }); },
      save() {}, restore() {}, rect() {}, clip() {},
    };
    return { id, width: 454, height: 240, getContext: () => context, context };
  }
  const elements = {
    "monitor-stage": { style: {} },
    dashboard: { setAttribute() {} },
    "router-chart": canvas("router-chart"),
    "devices-chart": canvas("devices-chart"),
    "router-sample-state": {},
  };
  const streams = {};
  const errors = [];
  vm.runInNewContext(source, {
    document: { getElementById: id => elements[id] || null },
    window: { innerWidth: 1080, innerHeight: 1920, setInterval() {}, addEventListener() {} },
    EventSource: function (url) { streams[url] = this; },
    console: { error: error => errors.push(error) },
  });
  function packet(data) {
    streams["/api/wan/events"].onmessage({ data: JSON.stringify(data) });
    assert.equal(errors.length, 0, String(errors[0]));
  }
  function general(time) {
    streams["/api/events"].onmessage({ data: JSON.stringify({ server_time: time, beszel: { systems: [] } }) });
    assert.equal(errors.length, 0, String(errors[0]));
  }
  function wan(time, value, options = {}) {
    const sample = { enabled: true, status: "up", sampled_at: time, download_bps: value, upload_bps: value / 2, ...options };
    packet({ server_time: time, wan: sample, wan_generation: 1, wan_samples: [sample] });
  }
  return {
    router: elements["router-chart"].context,
    devices: elements["devices-chart"].context,
    packet,
    general,
    wan,
    state: elements["router-sample-state"],
    emit(time, value, options = {}) {
      general(time);
      wan(time, value, options);
    },
  };
}

function downloadPaths(chart) { return chart.paths.filter(path => path.dash.length === 0); }

test("charts start at 50 Mbps with accurate quarter ticks", () => {
  const app = dashboard();
  app.emit(1000, 10000000);
  assert.deepEqual(app.router.labels, ["50M", "37.5M", "25M", "12.5M", "0"]);
  assert.equal(app.devices.labels[0], "50M");
});

test("scales use 50 Mbps multiples and return after the peak leaves the window", () => {
  const app = dashboard();
  app.emit(1000, 50000000);
  assert.equal(app.router.labels[0], "50M");
  app.emit(1002, 60000000);
  assert.equal(app.router.labels[0], "100M");
  app.emit(1121, 1000000);
  assert.equal(app.router.labels[0], "100M");
  app.emit(1123, 1000000);
  assert.equal(app.router.labels[0], "50M");
  app.emit(1124, 110000000);
  assert.equal(app.router.labels[0], "150M");
  app.emit(1125, 201000000);
  assert.equal(app.router.labels[0], "250M");
  app.emit(1246, 1000000);
  assert.equal(app.router.labels[0], "50M");
  assert.equal(app.devices.labels[0], "50M");
});

test("few samples occupy their true time span instead of stretched triangles", () => {
  const app = dashboard();
  app.emit(1000, 10000000);
  app.emit(1002, 20000000);
  const vertices = downloadPaths(app.router)[0].vertices;
  assert.equal(vertices.length, 3);
  assert.equal(vertices[1].type, "line");
  assert.equal(vertices[1].x, vertices[0].x);
  const dx = vertices[2].x - vertices[0].x;
  assert.ok(Math.abs(dx - 378 * 2 / 120) < 0.000001);
});

test("cached SNMP responses do not add another point", () => {
  const app = dashboard();
  app.emit(1000, 10000000);
  app.emit(1001, 10000000, { sampled_at: 1000 });
  assert.equal(downloadPaths(app.router).length, 0);
  assert.equal(app.router.dots.length, 2);
});

test("failed samples create a gap, not a dive to zero", () => {
  const app = dashboard();
  app.emit(1000, 10000000);
  app.emit(1002, 20000000);
  app.emit(1004, null, { status: "error", upload_bps: null });
  app.emit(1006, 30000000);
  const paths = downloadPaths(app.router);
  assert.equal(paths.length, 1);
  assert.equal(paths[0].vertices.length, 3);
  assert.ok(paths[0].vertices[0].y < 222);
  assert.ok(paths[0].vertices[2].y < 222);
  assert.equal(app.router.dots.length, 2);
});

test("long sampling outages are not bridged by invented data", () => {
  const app = dashboard();
  app.emit(1000, 10000000);
  app.emit(1050, 20000000);
  assert.equal(downloadPaths(app.router).length, 0);
  assert.equal(app.router.dots.length, 4);
});

test("router rates are horizontal interval segments, never widening cubic peaks", () => {
  const app = dashboard();
  [5, 40, 5, 30, 30, 20].forEach((value, index) => app.emit(1000 + index * 2, value * 1000000));
  const vertices = downloadPaths(app.router)[0].vertices;
  assert.equal(vertices.length, 11);
  for (let i = 1; i < vertices.length; i += 2) {
    assert.equal(vertices[i].type, "line");
    assert.equal(vertices[i].x, vertices[i - 1].x);
    assert.equal(vertices[i].y, vertices[i + 1].y);
  }
});

test("true zero traffic remains visible at the baseline", () => {
  const app = dashboard();
  app.emit(1000, 0);
  app.emit(1002, 0);
  const vertices = downloadPaths(app.router)[0].vertices;
  assert.equal(vertices[0].y, 222);
  assert.equal(vertices[2].y, 222);
});

test("WAN charts render before any slow general dashboard response", () => {
  const app = dashboard();
  app.wan(1000, 60000000);
  app.wan(1001, 60000000);
  assert.equal(app.router.labels[0], "100M");
  assert.equal(downloadPaths(app.router).length, 1);
  app.general(990);
  assert.equal(app.router.labels[0], "100M");
  assert.equal(downloadPaths(app.router).length, 1);
});

test("reconnect backfills more than 60 one-second samples from the shared history", () => {
  const app = dashboard();
  const samples = Array.from({ length: 100 }, (_, i) => ({
    status: "up", sampled_at: 1000 + i, download_bps: 10000000, upload_bps: 1000000,
  }));
  app.packet({ server_time: 1099, wan_generation: 1, wan: samples[99], wan_samples: samples });
  assert.equal(downloadPaths(app.router)[0].vertices.length, 199);
});

test("heartbeat alone releases an old expanded scale", () => {
  const app = dashboard();
  app.wan(1000, 90000000);
  app.packet({ server_time: 1121, wan_generation: 1, wan: { status: "up" }, wan_samples: [] });
  assert.equal(app.router.labels[0], "50M");
});

test("a held rate is visibly marked as waiting for the device counter", () => {
  const app = dashboard();
  app.wan(1000, 10000000, { download_held: true });
  assert.equal(app.state.textContent, "等待计数器刷新");
  app.wan(1001, 10000000, { download_held: false });
  assert.equal(app.state.textContent, "");
});
