/* Web 遥控前端逻辑：WebSocket 控制 + MJPEG 摄像头 + 方向键。 */

(function () {
  "use strict";

  var WS_URL = (location.protocol === "https:" ? "wss://" : "ws://") + location.host + "/ws";
  var CMD_PERIOD_MS = 100; // 10Hz，与后端 publish_rate 对应

  var ws = null;
  var token = "";
  var retryTimer = null;
  var busy = false;

  var maxLinear = 0.3;
  var maxAngular = 1.0;

  var activeDirection = null;

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

  // ---------------- 锁存方向键 ----------------
  function bindDpad() {
    var buttons = document.querySelectorAll(".dpad-btn");
    function selectDirection(dir) {
      activeDirection = dir;
      buttons.forEach(function (button) {
        button.classList.toggle("pressed", button.getAttribute("data-dir") === dir);
      });
      pushCommand();
    }
    buttons.forEach(function (btn) {
      var dir = btn.getAttribute("data-dir");
      btn.addEventListener("pointerdown", function (e) {
        e.preventDefault();
        selectDirection(dir);
      });
      // 键盘聚焦按钮后按 Enter/Space 时没有 pointerdown，使用 click 兜底。
      btn.addEventListener("click", function (e) {
        if (e.detail === 0) { selectDirection(dir); }
      });
      ["contextmenu", "selectstart", "dragstart"].forEach(function (eventName) {
        btn.addEventListener(eventName, function (e) { e.preventDefault(); });
      });
    });
  }

  function stopMotion(forceSend) {
    var hadActiveCommand = wasNonzero;
    activeDirection = null;
    document.querySelectorAll(".dpad-btn").forEach(function (btn) {
      btn.classList.remove("pressed");
    });
    pushCommand();
    if (forceSend && !hadActiveCommand) { send({ type: "stop" }); }
  }

  // ---------------- 键盘（桌面调试） ----------------
  document.addEventListener("keydown", function (e) {
    if (e.repeat) { return; }
    var key = e.key.toLowerCase();
    var dir = null;
    if (key === "w" || key === "arrowup") { dir = "up"; }
    else if (key === "s" || key === "arrowdown") { dir = "down"; }
    else if (key === "a" || key === "arrowleft") { dir = "left"; }
    else if (key === "d" || key === "arrowright") { dir = "right"; }
    else if (key === " " || key === "k") { stopMotion(true); }
    if (dir) {
      activeDirection = dir;
      document.querySelectorAll(".dpad-btn").forEach(function (btn) {
        btn.classList.toggle("pressed", btn.getAttribute("data-dir") === dir);
      });
      pushCommand();
    }
    if (dir || key === " " || key === "k") { e.preventDefault(); }
  });

  // ---------------- 滑条 ----------------
  function onSlider() {
    maxLinear = parseFloat(linearSlider.value);
    maxAngular = parseFloat(angularSlider.value);
    linearValue.textContent = maxLinear.toFixed(2);
    angularValue.textContent = maxAngular.toFixed(2);
    send({ type: "speed", max_linear: maxLinear, max_angular: maxAngular });
    if (activeDirection) { pushCommand(); }
  }
  linearSlider.addEventListener("input", onSlider);
  angularSlider.addEventListener("input", onSlider);

  // ---------------- 按钮 ----------------
  $("btn-stop").addEventListener("pointerdown", function (e) {
    e.preventDefault();
    stopMotion(true);
  });
  $("btn-stop").addEventListener("click", function (e) {
    if (e.detail === 0) { stopMotion(true); }
  });
  ["contextmenu", "selectstart", "dragstart"].forEach(function (eventName) {
    $("btn-stop").addEventListener(eventName, function (e) { e.preventDefault(); });
  });
  $("btn-estop").addEventListener("click", function () { send({ type: "estop" }); });
  $("btn-unlock").addEventListener("click", function () { send({ type: "unlock" }); });

  // 页面离开或失去焦点时必须清零，避免触点丢失后继续运动。
  window.addEventListener("blur", function () { stopMotion(true); });
  window.addEventListener("pagehide", function () { stopMotion(true); });
  document.addEventListener("visibilitychange", function () {
    if (document.hidden) { stopMotion(true); }
  });

  // ---------------- 启动 ----------------
  bindDpad();
  connect();
})();
