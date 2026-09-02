/* XuegeCar 遥控前端逻辑：WebSocket 控制 + MJPEG 摄像头 + 摇杆/按键。 */

(function () {
  "use strict";

  var WS_URL = (location.protocol === "https:" ? "wss://" : "ws://") + location.host + "/ws";
  var CMD_PERIOD_MS = 100; // 10Hz，与后端 publish_rate 对应

  var ws = null;
  var token = "";
  var retryTimer = null;
  var busy = false;

  var mode = "joystick"; // joystick | dpad
  var maxLinear = 0.3;
  var maxAngular = 1.0;

  var joystickVec = { x: 0, y: 0 }; // 归一化 [-1,1]，y 向下为正
  var dpadState = { up: false, down: false, left: false, right: false };
  var keyboardState = { up: false, down: false, left: false, right: false };

  // ---------------- DOM ----------------
  var $ = function (id) { return document.getElementById(id); };
  var pillConn = $("pill-conn"), pillBattery = $("pill-battery"), pillCamera = $("pill-camera");
  var cameraImg = $("camera"), cameraPlaceholder = $("camera-placeholder");
  var cameraFps = $("camera-fps"), speedReadout = $("speed-readout");
  var linearSlider = $("linear-slider"), angularSlider = $("angular-slider");
  var linearValue = $("linear-value"), angularValue = $("angular-value");
  var overlay = $("overlay"), overlayTitle = $("overlay-title"), overlayDesc = $("overlay-desc");

  // ---------------- WebSocket ----------------
  function connect() {
    ws = new WebSocket(WS_URL);
    ws.onopen = function () { /* 等待 welcome */ };
    ws.onmessage = function (event) {
      var msg;
      try { msg = JSON.parse(event.data); } catch (e) { return; }
      handleMessage(msg);
    };
    ws.onclose = function (event) {
      var wasBusy = busy;
      busy = false;
      cameraImg.src = "";
      cameraPlaceholder.classList.remove("hidden");
      if (wasBusy || event.code === 4008) {
        showOverlay("控制权被占用", "当前控制者 IP：" + (busyOwner || "未知") + "，等待其断开…");
        busy = true;
      } else {
        showOverlay("连接断开", "正在重连…");
      }
      scheduleReconnect(wasBusy ? 2500 : 1500);
    };
    ws.onerror = function () { /* onclose 会随后触发 */ };
  }

  var busyOwner = "";
  function handleMessage(msg) {
    if (msg.type === "busy") {
      busyOwner = msg.owner_ip || "未知";
      busy = true;
      showOverlay("控制权被占用", "当前控制者 IP：" + busyOwner + "，等待其断开…");
      try { ws.close(4008); } catch (e) { /* ignore */ }
      return;
    }
    if (msg.type === "welcome") {
      busy = false;
      token = msg.token || "";
      hideOverlay();
      if (token) {
        cameraImg.src = "/stream?token=" + encodeURIComponent(token);
      }
      send({ type: "speed", max_linear: maxLinear, max_angular: maxAngular });
      applyState(msg.state || {});
      return;
    }
    if (msg.type === "state") {
      applyState(msg);
    }
  }

  function applyState(state) {
    // 连接状态
    if (state.estop_locked) {
      pillConn.textContent = "急停锁止";
      pillConn.className = "pill err";
    } else {
      pillConn.textContent = "已连接";
      pillConn.className = "pill ok";
    }
    // 电池
    if (state.battery_percent != null) {
      pillBattery.textContent = "电池 " + Math.round(state.battery_percent) + "%";
      pillBattery.className = "pill " + (state.battery_percent > 20 ? "ok" : "warn");
    } else if (state.battery_voltage != null) {
      pillBattery.textContent = "电压 " + state.battery_voltage.toFixed(2) + " V";
      pillBattery.className = "pill";
    } else {
      pillBattery.textContent = "电池 --";
      pillBattery.className = "pill";
    }
    // 摄像头
    var age = state.camera_age;
    if (age == null || age < 0 || age > 3) {
      pillCamera.textContent = "摄像头无信号";
      pillCamera.className = "pill warn";
      cameraPlaceholder.classList.remove("hidden");
      cameraFps.textContent = "-- fps";
    } else {
      pillCamera.textContent = "摄像头在线";
      pillCamera.className = "pill ok";
      cameraPlaceholder.classList.add("hidden");
      cameraFps.textContent = (state.camera_fps || 0).toFixed(1) + " fps";
    }
    // 速度读数
    speedReadout.textContent =
      "下发 " + (state.cmd_linear || 0).toFixed(2) + " m/s · " +
      (state.cmd_angular || 0).toFixed(2) + " rad/s | 实际 " +
      (state.odom_linear || 0).toFixed(2) + " m/s · " +
      (state.odom_angular || 0).toFixed(2) + " rad/s";
    // 急停按钮样式
    $("btn-estop").classList.toggle("locked", !!state.estop_locked);
  }

  function scheduleReconnect(delay) {
    if (retryTimer) { clearTimeout(retryTimer); }
    retryTimer = setTimeout(connect, delay);
  }

  function send(obj) {
    if (ws && ws.readyState === WebSocket.OPEN) {
      ws.send(JSON.stringify(obj));
    }
  }

  // ---------------- 遮罩 ----------------
  function showOverlay(title, desc) {
    overlayTitle.textContent = title;
    overlayDesc.textContent = desc;
    overlay.classList.remove("hidden");
  }
  function hideOverlay() {
    overlay.classList.add("hidden");
  }

  // ---------------- 命令合成 ----------------
  function currentCommand() {
    var linear = 0, angular = 0;
    if (mode === "joystick") {
      linear = -joystickVec.y * maxLinear;
      angular = -joystickVec.x * maxAngular;
    } else {
      var up = dpadState.up || keyboardState.up;
      var down = dpadState.down || keyboardState.down;
      var left = dpadState.left || keyboardState.left;
      var right = dpadState.right || keyboardState.right;
      linear = ((up ? 1 : 0) - (down ? 1 : 0)) * maxLinear;
      angular = ((left ? 1 : 0) - (right ? 1 : 0)) * maxAngular;
    }
    return { linear: linear, angular: angular };
  }

  function pushCommand() {
    var cmd = currentCommand();
    var nonzero = Math.abs(cmd.linear) > 1e-6 || Math.abs(cmd.angular) > 1e-6;
    if (nonzero) {
      wasNonzero = true;
      send({ type: "cmd", linear: cmd.linear, angular: cmd.angular });
    } else if (wasNonzero) {
      // 松手瞬间：发一次 stop，后端零速连发后静默，释放 twist_mux 仲裁。
      wasNonzero = false;
      send({ type: "stop" });
    }
    // 全零时不发送：避免持续占用 twist_mux 手动输入，阻塞导航/Agent 源。
  }

  var wasNonzero = false;
  // 10Hz 合成循环：仅在有非零命令时下发。
  setInterval(pushCommand, CMD_PERIOD_MS);
  // 3s 心跳保活：空闲浏览时不被 session_timeout 踢下线。
  setInterval(function () { send({ type: "ping" }); }, 3000);

  // ---------------- 摇杆 ----------------
  var joystickManager = null;
  function initJoystick() {
    var zone = $("joystick-zone");
    var available = Math.min(zone.clientWidth || 170, zone.clientHeight || 170);
    var joystickSize = Math.max(88, Math.min(170, available - 16));
    joystickManager = nipplejs.create({
      zone: $("joystick"),
      mode: "static",
      position: { left: "50%", top: "50%" },
      size: joystickSize,
      restJoystick: true,
      color: "#2f80ed",
      fadeTime: 80,
    });
    var half = joystickSize / 2;
    joystickManager.on("move", function (evt, data) {
      joystickVec.x = Math.max(-1, Math.min(1, data.vector.x / half));
      joystickVec.y = Math.max(-1, Math.min(1, data.vector.y / half));
    });
    joystickManager.on("end", function () {
      joystickVec.x = 0;
      joystickVec.y = 0;
    });
  }

  // ---------------- 按键（多点触控） ----------------
  function bindDpad() {
    var buttons = document.querySelectorAll(".dpad-btn");
    function setDir(dir, pressed) {
      if (!(dir in dpadState)) { return; }
      dpadState[dir] = pressed;
      var btn = document.querySelector('.dpad-btn[data-dir="' + dir + '"]');
      if (btn) { btn.classList.toggle("pressed", pressed); }
    }
    buttons.forEach(function (btn) {
      var dir = btn.getAttribute("data-dir");
      btn.addEventListener("pointerdown", function (e) {
        e.preventDefault();
        btn.setPointerCapture(e.pointerId);
        setDir(dir, true);
      });
      var release = function (e) {
        e.preventDefault();
        setDir(dir, false);
      };
      btn.addEventListener("pointerup", release);
      btn.addEventListener("pointercancel", release);
    });
  }

  // ---------------- 键盘（桌面调试） ----------------
  document.addEventListener("keydown", function (e) {
    if (e.repeat) { return; }
    var key = e.key.toLowerCase();
    if (key === "w" || key === "arrowup") { keyboardState.up = true; }
    else if (key === "s" || key === "arrowdown") { keyboardState.down = true; }
    else if (key === "a" || key === "arrowleft") { keyboardState.left = true; }
    else if (key === "d" || key === "arrowright") { keyboardState.right = true; }
    else if (key === " ") { send({ type: "stop" }); }
  });
  document.addEventListener("keyup", function (e) {
    var key = e.key.toLowerCase();
    if (key === "w" || key === "arrowup") { keyboardState.up = false; }
    else if (key === "s" || key === "arrowdown") { keyboardState.down = false; }
    else if (key === "a" || key === "arrowleft") { keyboardState.left = false; }
    else if (key === "d" || key === "arrowright") { keyboardState.right = false; }
  });

  // ---------------- 标签页 ----------------
  var tabs = document.querySelectorAll(".tab");
  tabs.forEach(function (tab) {
    tab.addEventListener("click", function () {
      tabs.forEach(function (t) { t.classList.remove("active"); });
      tab.classList.add("active");
      mode = tab.getAttribute("data-mode");
      $("joystick-panel").classList.toggle("hidden", mode !== "joystick");
      $("dpad-panel").classList.toggle("hidden", mode !== "dpad");
      // 切换模式时清零另一模式输入。
      joystickVec.x = joystickVec.y = 0;
      dpadState.up = dpadState.down = dpadState.left = dpadState.right = false;
      document.querySelectorAll(".dpad-btn").forEach(function (b) {
        b.classList.remove("pressed");
      });
    });
  });

  // ---------------- 滑条 ----------------
  function onSlider() {
    maxLinear = parseFloat(linearSlider.value);
    maxAngular = parseFloat(angularSlider.value);
    linearValue.textContent = maxLinear.toFixed(2);
    angularValue.textContent = maxAngular.toFixed(2);
    send({ type: "speed", max_linear: maxLinear, max_angular: maxAngular });
  }
  linearSlider.addEventListener("input", onSlider);
  angularSlider.addEventListener("input", onSlider);

  // ---------------- 按钮 ----------------
  $("btn-stop").addEventListener("click", function () {
    joystickVec.x = joystickVec.y = 0;
    dpadState.up = dpadState.down = dpadState.left = dpadState.right = false;
    send({ type: "stop" });
  });
  $("btn-estop").addEventListener("click", function () { send({ type: "estop" }); });
  $("btn-unlock").addEventListener("click", function () { send({ type: "unlock" }); });

  // ---------------- 启动 ----------------
  initJoystick();
  bindDpad();
  connect();
})();
