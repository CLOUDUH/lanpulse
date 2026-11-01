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
  };
  let events;
  const errors = [];
  vm.runInNewContext(source, {
    document: { getElementById: id => elements[id] || null },
    window: { innerWidth: 1080, innerHeight: 1920, setInterval() {}, addEventListener() {} },
    EventSource: function () { events = this; },
    console: { error: error => errors.push(error) },
  });
  return {
    router: elements["router-chart"].context,
    devices: elements["devices-chart"].context,
    emit(time, value, options = {}) {
      const data = {
        server_time: time,
        wan: { enabled: true, status: "up", sampled_at: time, download_bps: value, upload_bps: value / 2, ...options },
        beszel: { systems: [] },
      };
      events.onmessage({ data: JSON.stringify(data) });
      assert.equal(errors.length, 0, String(errors[0]));
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

test("scales expand only when exceeded and do not bounce back", () => {
  const app = dashboard();
  app.emit(1000, 50000000);
  assert.equal(app.router.labels[0], "50M");
  app.emit(1002, 60000000);
  assert.equal(app.router.labels[0], "100M");
  app.emit(1200, 1000000);
  assert.equal(app.router.labels[0], "100M");
  assert.equal(app.devices.labels[0], "50M");
});

test("few samples occupy their true time span instead of stretched triangles", () => {
  const app = dashboard();
  app.emit(1000, 10000000);
  app.emit(1002, 20000000);
  const vertices = downloadPaths(app.router)[0].vertices;
  assert.equal(vertices.length, 2);
  assert.equal(vertices[1].type, "curve");
  const dx = vertices[1].values[4] - vertices[0].x;
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
  assert.equal(paths[0].vertices.length, 2);
  assert.ok(paths[0].vertices[0].y < 222);
  assert.ok(paths[0].vertices[1].values[5] < 222);
  assert.equal(app.router.dots.length, 2);
});

test("long sampling outages are not bridged by invented data", () => {
  const app = dashboard();
  app.emit(1000, 10000000);
  app.emit(1050, 20000000);
  assert.equal(downloadPaths(app.router).length, 0);
  assert.equal(app.router.dots.length, 4);
});

test("smooth segments stay within the actual adjacent sample values", () => {
  const app = dashboard();
  [5, 40, 5, 30, 30, 20].forEach((value, index) => app.emit(1000 + index * 2, value * 1000000));
  const vertices = downloadPaths(app.router)[0].vertices;
  let fromY = vertices[0].y;
  for (const vertex of vertices.slice(1)) {
    const [, firstY, , secondY, , toY] = vertex.values;
    const low = Math.min(fromY, toY), high = Math.max(fromY, toY);
    assert.ok(firstY >= low - 1e-9 && firstY <= high + 1e-9);
    assert.ok(secondY >= low - 1e-9 && secondY <= high + 1e-9);
    fromY = toY;
  }
});

test("true zero traffic remains visible at the baseline", () => {
  const app = dashboard();
  app.emit(1000, 0);
  app.emit(1002, 0);
  const vertices = downloadPaths(app.router)[0].vertices;
  assert.equal(vertices[0].y, 222);
  assert.equal(vertices[1].values[5], 222);
});
