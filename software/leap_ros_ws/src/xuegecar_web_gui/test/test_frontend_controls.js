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
  }
  getAttribute(name) { return this.attributes[name]; }
  addEventListener(name, callback) {
    (this.listeners[name] ||= []).push(callback);
  }
  setPointerCapture() {}
  dispatch(name, properties = {}) {
    const event = Object.assign({
      type: name,
      pointerId: 0,
      defaultPrevented: false,
      preventDefault() { this.defaultPrevented = true; },
    }, properties);
    (this.listeners[name] || []).forEach((callback) => callback(event));
    return event;
  }
}

const ids = [
  "pill-conn", "pill-battery", "pill-camera", "camera", "camera-placeholder",
  "camera-fps", "speed-readout", "linear-slider", "angular-slider",
  "linear-value", "angular-value", "overlay", "overlay-title", "overlay-desc",
  "btn-stop", "btn-estop", "btn-unlock",
];
const elements = Object.fromEntries(ids.map((id) => [id, new Element(id)]));
elements["linear-slider"].value = "0.3";
elements["angular-slider"].value = "1.0";

const directions = ["up", "down", "left", "right"];
const dpadButtons = directions.map((dir) => new Element("dpad-" + dir, { "data-dir": dir }));
const documentListeners = {};
const document = {
  hidden: false,
  getElementById(id) { return elements[id]; },
  querySelectorAll(selector) {
    return selector === ".dpad-btn" ? dpadButtons : [];
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

const context = {
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

up.dispatch("pointerdown", { pointerId: 11 });
assert(up.classList.contains("pressed"), "按下后应显示 pressed 效果");
up.dispatch("pointerup", { pointerId: 11 });
intervals[0]();
intervals[0]();
intervals[0]();
let commands = socket.sent.filter((message) => message.type === "cmd");
assert.strictEqual(commands.length, 4, "点击松手后仍应按 10Hz 持续发送");
assert(commands.every((message) => message.linear === 0.3 && message.angular === 0));
assert(up.classList.contains("pressed"), "松手后当前方向应保持选中状态");

left.dispatch("pointerdown", { pointerId: 12 });
assert.deepStrictEqual(socket.sent.at(-1), { type: "cmd", linear: 0, angular: 1 });
assert(!up.classList.contains("pressed"), "新方向应取消旧方向的选中状态");
assert(left.classList.contains("pressed"), "新方向应保持选中状态");

left.dispatch("pointerup", { pointerId: 12 });
intervals[0]();
assert.deepStrictEqual(socket.sent.at(-1), { type: "cmd", linear: 0, angular: 1 }, "松手不应停止");

elements["btn-stop"].dispatch("pointerdown", { pointerId: 13 });
assert.deepStrictEqual(socket.sent.at(-1), { type: "stop" }, "中间停止按钮应立即停车");
assert(!left.classList.contains("pressed"), "停车后应清除方向选中状态");

const menuEvent = up.dispatch("contextmenu");
assert(menuEvent.defaultPrevented, "长按菜单必须被阻止");

console.log("frontend controls: latched direction/replace/stop/contextmenu PASS");
