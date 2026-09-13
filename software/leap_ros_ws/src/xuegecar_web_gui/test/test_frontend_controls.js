"use strict";

const assert = require("assert");
const fs = require("fs");
const path = require("path");
const vm = require("vm");

class ClassList {
  constructor() { this.names = new Set(); }
  add(name) { this.names.add(name); }
  remove(name) { this.names.delete(name); }
  toggle(name, enabled) {
    if (enabled === undefined) {
      enabled = !this.names.has(name);
    }
    enabled ? this.names.add(name) : this.names.delete(name);
    return enabled;
  }
  contains(name) { return this.names.has(name); }
}

class Element {
  constructor(id, attributes = {}) {
    this.id = id;
    this.attributes = attributes;
    this.classList = new ClassList();
    this.listeners = {};
    this.value = "";
    this.textContent = "";
    this.src = "";
    this.style = {};
    this.hidden = false;
  }
  getAttribute(name) { return this.attributes[name]; }
  addEventListener(name, callback) {
    (this.listeners[name] ||= []).push(callback);
  }
  setPointerCapture(id) { this.capturedPointer = id; }
  releasePointerCapture(id) { if (this.capturedPointer === id) { this.capturedPointer = null; } }
  setAttribute(name, value) { this.attributes[name] = value; }
  getBoundingClientRect() { return { left: 0, top: 0, width: 200, height: 200 }; }
  dispatch(name, properties = {}) {
    const event = Object.assign({
      type: name,
      pointerId: 0,
      defaultPrevented: false,
      preventDefault() { this.defaultPrevented = true; },
      stopPropagation() {},
    }, properties);
    (this.listeners[name] || []).forEach((callback) => callback(event));
    return event;
  }
}

const ids = [
  "pill-conn", "pill-battery", "pill-camera", "camera", "camera-placeholder",
  "camera-fps", "speed-readout", "linear-slider", "angular-slider",
  "linear-value", "angular-value", "overlay", "overlay-title", "overlay-desc",
  "btn-estop", "btn-unlock", "joystick", "joystick-knob", "dpad-panel", "joystick-panel",
];
const elements = Object.fromEntries(ids.map((id) => [id, new Element(id)]));
elements["linear-slider"].value = "0.3";
elements["angular-slider"].value = "1.0";

const directions = ["up", "down", "left", "right"];
const dpadButtons = directions.map((dir) => new Element("dpad-" + dir, { "data-dir": dir }));
const modeButtons = ["dpad", "joystick"].map((mode) => new Element(mode, { "data-mode": mode }));
const documentListeners = {};
const document = {
  hidden: false,
  getElementById(id) { return elements[id]; },
  querySelectorAll(selector) {
    return selector === ".dpad-btn" ? dpadButtons : selector === ".control-mode" ? modeButtons : [];
  },
  querySelector(selector) {
    const match = selector.match(/^\.dpad-btn\[data-dir="(\w+)"\]$/);
    return match ? dpadButtons.find((button) => button.getAttribute("data-dir") === match[1]) : null;
  },
  addEventListener(name, callback) {
    (documentListeners[name] ||= []).push(callback);
  },
};

const windowListeners = {};
const window = {
  addEventListener(name, callback) {
    (windowListeners[name] ||= []).push(callback);
  },
};
const intervals = [];

class FakeWebSocket {
  static OPEN = 1;
  static instances = [];
  constructor(url) {
    this.url = url;
    this.readyState = FakeWebSocket.OPEN;
    this.sent = [];
    FakeWebSocket.instances.push(this);
  }
  send(message) { this.sent.push(JSON.parse(message)); }
  close() {}
}

let frontendNow = 1000;
const context = {
  performance: { now() { return frontendNow; } },
  console,
  document,
  window,
  location: { protocol: "http:", host: "localhost:8000" },
  WebSocket: FakeWebSocket,
  setInterval(callback) { intervals.push(callback); return intervals.length; },
  setTimeout() { return 1; },
  clearTimeout() {},
  encodeURIComponent,
};
vm.createContext(context);
const appPath = path.join(__dirname, "..", "xuegecar_web_gui", "static", "app.js");
vm.runInContext(fs.readFileSync(appPath, "utf8"), context, { filename: appPath });

const socket = FakeWebSocket.instances[0];
const up = dpadButtons.find((button) => button.getAttribute("data-dir") === "up");
const left = dpadButtons.find((button) => button.getAttribute("data-dir") === "left");

socket.onmessage({ data: JSON.stringify({ type: "welcome", token: "test", state: {
  lease_deadline: 1234, lease_duration_ms: 100,
} }) });
function renewLease() {
  socket.onmessage({ data: JSON.stringify({ type: "state", lease_deadline: 1234,
    lease_duration_ms: 100, estop_locked: false }) });
}
function dispatchDocument(name, properties) {
  const event = Object.assign({ preventDefault() {} }, properties);
  (documentListeners[name] || []).forEach((callback) => callback(event));
}
function last() {
  const message = { ...socket.sent.at(-1) };
  delete message.lease_deadline;
  return message;
}
function assertIdle() {
  const count = socket.sent.length;
  intervals[0](); intervals[0]();
  assert.strictEqual(socket.sent.length, count, "停车后不能继续下发运动命令");
}

up.dispatch("pointerdown", { pointerId: 11 });
assert.strictEqual(up.capturedPointer, 11, "需捕获指针以接收按钮外的松手事件");
assert(up.classList.contains("pressed"));
intervals[0](); intervals[0]();
assert.deepStrictEqual(last(), { type: "cmd", linear: 0.3, angular: 0 });
up.dispatch("pointerup", { pointerId: 99 });
assert(up.classList.contains("pressed"), "其他触点松手不能打断当前输入");
left.dispatch("pointerdown", { pointerId: 12 });
assert(!left.classList.contains("pressed"), "多触点不得抢占当前输入");
up.dispatch("pointerup", { pointerId: 11 });
assert.deepStrictEqual(last(), { type: "stop" }, "松手立即停车");
assert(!up.classList.contains("pressed"));
assertIdle();

for (const release of ["pointercancel", "lostpointercapture"]) {
  left.dispatch("pointerdown", { pointerId: 12 });
  assert.deepStrictEqual(last(), { type: "cmd", linear: 0, angular: 1 });
  left.dispatch(release, { pointerId: 12 });
  assert.deepStrictEqual(last(), { type: "stop" });
  assertIdle();
}
up.dispatch("click", { detail: 0 });
assertIdle();
assert(up.dispatch("contextmenu").defaultPrevented);
up.dispatch("pointerdown", { button: 2 });
assertIdle();

dispatchDocument("keydown", { key: "w" });
assert.deepStrictEqual(last(), { type: "cmd", linear: 0.3, angular: 0 });
dispatchDocument("keyup", { key: "a" });
intervals[0]();
assert.strictEqual(last().type, "cmd");
dispatchDocument("keyup", { key: "w" });
assert.deepStrictEqual(last(), { type: "stop" });
assertIdle();
for (const key of ["Enter", " "]) {
  left.dispatch("keydown", { key });
  assert.deepStrictEqual(last(), { type: "cmd", linear: 0, angular: 1 });
  dispatchDocument("keyup", { key });
  assert.deepStrictEqual(last(), { type: "stop" });
}

up.dispatch("pointerdown", { pointerId: 20 });
modeButtons[1].dispatch("click");
assert.deepStrictEqual(last(), { type: "stop" }, "切换模式需停车");
assert(elements["dpad-panel"].hidden);
assert(!elements["joystick-panel"].hidden);
const joystick = elements.joystick;
joystick.dispatch("pointerdown", { pointerId: 30, clientX: 135, clientY: 65 });
assert.deepStrictEqual(last(), { type: "cmd", linear: 0.15, angular: -0.5 }, "斜推同时输出前进与转向");
intervals[0]();
assert.strictEqual(last().type, "cmd");
joystick.dispatch("pointermove", { pointerId: 30, clientX: 1000, clientY: -1000 });
assert(Math.abs(last().linear) <= 0.3 && Math.abs(last().angular) <= 1, "摇杆超出圆盘时速度不得超限");
joystick.dispatch("pointermove", { pointerId: 30, clientX: 101, clientY: 101 });
assert.deepStrictEqual(last(), { type: "stop" }, "中心死区应停车");
joystick.dispatch("pointermove", { pointerId: 30, clientX: 65, clientY: 135 });
assert.deepStrictEqual(last(), { type: "cmd", linear: -0.15, angular: 0.5 });
elements["linear-slider"].value = "0.6";
elements["linear-slider"].dispatch("input");
assert.deepStrictEqual(last(), { type: "cmd", linear: -0.3, angular: 0.5 }, "摇杆沿用速度滑条");
joystick.dispatch("pointerup", { pointerId: 30 });
assert.deepStrictEqual(last(), { type: "stop" });
assert.strictEqual(elements["joystick-knob"].style.transform, "translate(0px, 0px)");
assertIdle();
for (const release of ["pointercancel", "lostpointercapture"]) {
  joystick.dispatch("pointerdown", { pointerId: 30, clientX: 100, clientY: 30 });
  joystick.dispatch(release, { pointerId: 30 });
  assert.deepStrictEqual(last(), { type: "stop" });
  assertIdle();
}
for (const eventName of ["blur", "pagehide", "visibilitychange"]) {
  joystick.dispatch("pointerdown", { pointerId: 31, clientX: 100, clientY: 30 });
  if (eventName === "visibilitychange") {
    document.hidden = true;
    dispatchDocument(eventName, {});
    document.hidden = false;
  } else {
    windowListeners[eventName].forEach((callback) => callback());
  }
  assert.deepStrictEqual(last(), { type: "stop" });
  assertIdle();
}
joystick.dispatch("pointerdown", { pointerId: 32, clientX: 100, clientY: 30 });
elements["btn-estop"].dispatch("click");
assert.deepStrictEqual(last(), { type: "estop" });
assertIdle();
joystick.dispatch("pointerdown", { pointerId: 33, clientX: 100, clientY: 30 });
assertIdle();
socket.onmessage({ data: JSON.stringify({ type: "state", estop_locked: false }) });
assertIdle();
joystick.dispatch("pointerdown", { pointerId: 34, clientX: 100, clientY: 30 });
assert.strictEqual(last().type, "cmd");
joystick.dispatch("pointerup", { pointerId: 34 });
socket.bufferedAmount = 1024 * 1024;
const beforeCongestion = socket.sent.length;
joystick.dispatch("pointerdown", { pointerId: 35, clientX: 100, clientY: 30 });
intervals[0]();
assert.strictEqual(socket.sent.length, beforeCongestion, "积压时不能排队运动命令");
socket.bufferedAmount = 0;
renewLease();
intervals[0]();
assert.strictEqual(last().type, "cmd", "积压期间开始按住，恢复后仍应续约");
joystick.dispatch("pointerup", { pointerId: 35 });
assertIdle();

modeButtons[0].dispatch("click");
up.dispatch("pointerdown", { pointerId: 36 });
for (let tick = 0; tick < 80; tick++) {
  frontendNow += 25;
  renewLease();
  socket.bufferedAmount = tick % 4 === 1 ? 1 : 0;
  const before = socket.sent.length;
  intervals[0]();
  assert(up.classList.contains("pressed"), "仍按住，短暂积压不能清除输入");
  if (socket.bufferedAmount) {
    assert.strictEqual(socket.sent.length, before, "积压期间跳过发送");
  } else {
    assert.deepStrictEqual(last(), { type: "cmd", linear: 0.6, angular: 0 });
    assert.strictEqual(socket.sent.at(-1).lease_deadline, 1234);
  }
}
socket.bufferedAmount = 1;
up.dispatch("pointerup", { pointerId: 36 });
assert.deepStrictEqual(last(), { type: "stop" });
assert(!up.classList.contains("pressed"));
socket.bufferedAmount = 0;
renewLease();
assertIdle();

dispatchDocument("keydown", { key: "w" });
frontendNow += 25;
socket.bufferedAmount = 1;
intervals[0]();
assert(up.classList.contains("pressed"));
dispatchDocument("keyup", { key: "w" });
assert.deepStrictEqual(last(), { type: "stop" });
socket.bufferedAmount = 0;
renewLease();
assertIdle();

up.dispatch("pointerdown", { pointerId: 37 });
frontendNow += 101;
const beforeMissingLease = socket.sent.length;
intervals[0]();
assert.strictEqual(socket.sent.length, beforeMissingLease, "状态租期不新鲜时不发送，交由后端到期停车");
assert(up.classList.contains("pressed"), "确认暂时中断时保留按住意图");
renewLease();
frontendNow += 25;
intervals[0]();
assert.strictEqual(last().type, "cmd", "仍按住时新鲜有效租期应继续续约");
up.dispatch("pointerup", { pointerId: 37 });
assertIdle();

up.dispatch("pointerdown", { pointerId: 38 });
frontendNow += 101;
renewLease();
const beforeBrowserPause = socket.sent.length;
intervals[0]();
assert.strictEqual(socket.sent.length, beforeBrowserPause, "长卡顿后先让待处理的松手事件执行");
up.dispatch("pointerup", { pointerId: 38 });
assert.deepStrictEqual(last(), { type: "stop" });
assertIdle();
renewLease();
modeButtons[1].dispatch("click");
joystick.dispatch("pointerdown", { pointerId: 39, clientX: 100, clientY: 30 });
socket.readyState = 3;
socket.onclose({ code: 1006 });
assertIdle();
assert.strictEqual(elements["joystick-knob"].style.transform, "translate(0px, 0px)");
console.log("frontend controls: hold/release/keyboard/joystick/limits/cancel/mode/estop/disconnect/lease/congestion/stall PASS");
