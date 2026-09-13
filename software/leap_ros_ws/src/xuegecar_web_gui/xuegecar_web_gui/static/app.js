/* Web 遥控前端逻辑：WebSocket 控制 + MJPEG 摄像头 + 方向键。 */

(function () {
  "use strict";

  var WS_URL = (location.protocol === "https:" ? "wss://" : "ws://") + location.host + "/ws";
  var CMD_PERIOD_MS = 25; // 40Hz，刷新 100ms 租期

  var ws = null;
  var token = "";
  var retryTimer = null;
  var busy = false;

  var maxLinear = 0.3;
  var maxAngular = 1.0;

  var activeDirection = null;
  var activeInput = null;
  var joystickX = 0, joystickY = 0;
  var controlMode = "dpad";
  var estopLocked = false;
  var leaseDeadline = 0;
  var leaseReceivedAt = 0;
  var leaseDurationMs = 100;
  var lastCommandTick = null;

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
      stopMotion(false);
      leaseDeadline = 0;
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
    if (typeof state.lease_deadline === "number") {
      leaseDeadline = state.lease_deadline;
      leaseReceivedAt = performance.now();
      leaseDurationMs = state.lease_duration_ms || 100;
    }
    estopLocked = !!state.estop_locked;
    if (estopLocked) { stopMotion(false); }
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
    if (activeInput && activeInput.kind === "joystick") {
      return { linear: -joystickY * maxLinear, angular: -joystickX * maxAngular };
    }
    if (activeDirection === "up") { return { linear: maxLinear, angular: 0 }; }
    if (activeDirection === "down") { return { linear: -maxLinear, angular: 0 }; }
    if (activeDirection === "left") { return { linear: 0, angular: maxAngular }; }
    if (activeDirection === "right") { return { linear: 0, angular: -maxAngular }; }
    return { linear: 0, angular: 0 };
  }

  function pushCommand() {
    var cmd = currentCommand();
    var nonzero = Math.abs(cmd.linear) > 1e-6 || Math.abs(cmd.angular) > 1e-6;
    if (nonzero) {
      var now = performance.now();
      if (!canDrive()) {
        stopMotion(false);
        return;
      }
      var longPause = lastCommandTick !== null && now - lastCommandTick >= leaseDurationMs;
      lastCommandTick = now;
      // 暂时不能续约不等于松手：保留输入、不堆积 cmd，后端租期自行到期。
      // 浏览器长卡顿后跳过一次刷新，让待处理的松手事件先执行。
      if (longPause || !canRefreshCommand(now)) { return; }
      wasNonzero = true;
      send({ type: "cmd", linear: cmd.linear, angular: cmd.angular, lease_deadline: leaseDeadline });
    } else if (wasNonzero) {
      // 松手瞬间：发一次 stop，后端零速连发后静默，释放 twist_mux 仲裁。
      wasNonzero = false;
      send({ type: "stop" });
    }
    // 全零时不发送：避免持续占用 twist_mux 手动输入，阻塞导航/Agent 源。
  }

  var wasNonzero = false;
  // 40Hz 合成循环：仅在有非零命令时下发。
  setInterval(pushCommand, CMD_PERIOD_MS);
  // 3s 心跳保活：空闲浏览时不被 session_timeout 踢下线。
  setInterval(function () { send({ type: "ping" }); }, 3000);

  // ---------------- 按住驾驶：只接受一个当前输入 ----------------
  function canDrive() {
    return !estopLocked && !busy && ws && ws.readyState === WebSocket.OPEN && !!token;
  }

  function canRefreshCommand(now) {
    return !ws.bufferedAmount && leaseDeadline > 0
      && now - leaseReceivedAt < leaseDurationMs;
  }

  function selectDirection(dir) {
    activeDirection = dir;
    document.querySelectorAll(".dpad-btn").forEach(function (button) {
      button.classList.toggle("pressed", button.getAttribute("data-dir") === dir);
    });
    pushCommand();
  }

  function bindPointerControl(element, kind, onMove) {
    element.addEventListener("pointerdown", function (e) {
      e.preventDefault();
      if ((e.button != null && e.button !== 0) || !canDrive() || activeInput) { return; }
      activeInput = { kind: kind, id: e.pointerId, element: element };
      try { element.setPointerCapture(e.pointerId); } catch (err) {
        stopMotion(false);
        return;
      }
      onMove(e);
    });
    element.addEventListener("pointermove", function (e) {
      if (activeInput && activeInput.element === element && activeInput.id === e.pointerId) {
        e.preventDefault();
        onMove(e);
      }
    });
    ["pointerup", "pointercancel", "lostpointercapture"].forEach(function (name) {
      element.addEventListener(name, function (e) {
        if (activeInput && activeInput.element === element && activeInput.id === e.pointerId) {
          stopMotion(false);
        }
      });
    });
    ["contextmenu", "selectstart", "dragstart"].forEach(function (name) {
      element.addEventListener(name, function (e) { e.preventDefault(); });
    });
  }

  function bindDpad() {
    document.querySelectorAll(".dpad-btn").forEach(function (btn) {
      bindPointerControl(btn, "dpad", function () { selectDirection(btn.getAttribute("data-dir")); });
      btn.addEventListener("keydown", function (e) {
        if (e.key !== "Enter" && e.key !== " ") { return; }
        e.preventDefault();
        e.stopPropagation();
        if (e.repeat || !canDrive() || activeInput) { return; }
        activeInput = { kind: "key", id: e.key.toLowerCase() };
        selectDirection(btn.getAttribute("data-dir"));
      });
    });
  }

  function moveJoystick(e) {
    var rect = $("joystick").getBoundingClientRect();
    var radius = Math.min(rect.width, rect.height) * 0.35;
    if (radius <= 0) { stopMotion(false); return; }
    var x = (e.clientX - rect.left - rect.width / 2) / radius;
    var y = (e.clientY - rect.top - rect.height / 2) / radius;
    var length = Math.sqrt(x * x + y * y);
    if (length > 1) { x /= length; y /= length; }
    // 中心死区避免手指轻微抖动导致爬行。
    joystickX = Math.abs(x) < 0.08 ? 0 : x;
    joystickY = Math.abs(y) < 0.08 ? 0 : y;
    $("joystick-knob").style.transform = "translate(" + (x * radius) + "px, " + (y * radius) + "px)";
    pushCommand();
  }

  function stopMotion(forceSend) {
    var hadActiveCommand = wasNonzero;
    var previousInput = activeInput;
    activeInput = null;
    activeDirection = null;
    lastCommandTick = null;
    joystickX = joystickY = 0;
    $("joystick-knob").style.transform = "translate(0px, 0px)";
    document.querySelectorAll(".dpad-btn").forEach(function (btn) {
      btn.classList.remove("pressed");
    });
    pushCommand();
    if (forceSend && !hadActiveCommand) { send({ type: "stop" }); }
    if (previousInput && previousInput.element) {
      try { previousInput.element.releasePointerCapture(previousInput.id); } catch (err) { /* already released */ }
    }
  }

  document.querySelectorAll(".control-mode").forEach(function (button) {
    button.addEventListener("click", function () {
      stopMotion(false);
      controlMode = button.getAttribute("data-mode");
      $("dpad-panel").hidden = controlMode !== "dpad";
      $("joystick-panel").hidden = controlMode !== "joystick";
      document.querySelectorAll(".control-mode").forEach(function (item) {
        item.setAttribute("aria-pressed", String(item === button));
      });
    });
  });

  // ---------------- 键盘 ----------------
  var keyDirections = { w: "up", arrowup: "up", s: "down", arrowdown: "down",
    a: "left", arrowleft: "left", d: "right", arrowright: "right" };
  document.addEventListener("keydown", function (e) {
    var key = e.key.toLowerCase();
    var dir = keyDirections[key];
    if (!dir || controlMode !== "dpad") { return; }
    if (e.target && /^(INPUT|TEXTAREA|SELECT)$/.test(e.target.tagName)) { return; }
    e.preventDefault();
    if (e.repeat || !canDrive() || activeInput) { return; }
    activeInput = { kind: "key", id: key };
    selectDirection(dir);
  });
  document.addEventListener("keyup", function (e) {
    if (activeInput && activeInput.kind === "key" && activeInput.id === e.key.toLowerCase()) {
      e.preventDefault();
      stopMotion(false);
    }
  });

  // ---------------- 滑条 ----------------
  function onSlider() {
    maxLinear = parseFloat(linearSlider.value);
    maxAngular = parseFloat(angularSlider.value);
    linearValue.textContent = maxLinear.toFixed(2);
    angularValue.textContent = maxAngular.toFixed(2);
    send({ type: "speed", max_linear: maxLinear, max_angular: maxAngular });
    if (activeInput) { pushCommand(); }
  }
  linearSlider.addEventListener("input", onSlider);
  angularSlider.addEventListener("input", onSlider);

  // ---------------- 按钮 ----------------
  $("btn-estop").addEventListener("click", function () {
    estopLocked = true;
    stopMotion(false);
    send({ type: "estop" });
  });
  $("btn-unlock").addEventListener("click", function () { send({ type: "unlock" }); });

  // 页面离开或失去焦点时必须清零，避免触点丢失后继续运动。
  window.addEventListener("blur", function () { stopMotion(true); });
  window.addEventListener("pagehide", function () { stopMotion(true); });
  document.addEventListener("visibilitychange", function () {
    if (document.hidden) { stopMotion(true); }
  });

  // ---------------- 启动 ----------------
  bindDpad();
  bindPointerControl($("joystick"), "joystick", moveJoystick);
  connect();
})();
