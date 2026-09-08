/* g1_api 地图编辑器：读取地图库的 .g1map（grid.pgm + manifest 航迹点），
 * 画障碍/擦除实时保存（PUT grid，同名覆盖），航迹点可视化编辑
 * （PUT tour-points，写回 manifest）。零外部依赖。 */
(function () {
  "use strict";

  var API = "";
  var state = {
    mapId: null, currentMapId: null,
    w: 0, h: 0, res: 0.05, ox: 0, oy: 0,
    grid: null,               // Uint8Array，行 0 = 图像顶部
    points: [],               // manifest tour_points（对象数组）
    mode: "draw", pen: 0, penSize: 2, zoom: 4,
    selected: -1, dragging: false, drawing: false,
    undoStack: [], lastCell: null,
    gridDirty: false, pointsDirty: false,
    extLib: [],               // 外部动作（回放）库列表，init 时拉取
  };

  var $ = function (id) { return document.getElementById(id); };
  var gridCanvas = $("gridCanvas"), overlay = $("overlayCanvas");
  var gtx = gridCanvas.getContext("2d"), otx = overlay.getContext("2d");

  function setStatus(text) { $("status").textContent = text; }

  /* ---------------- PGM ---------------- */
  function parsePgm(buf) {
    var bytes = new Uint8Array(buf), tokens = [], pos = 0, line, nl;
    while (tokens.length < 4 && pos < bytes.length) {
      nl = buf.byteLength; // find newline
      for (var i = pos; i < bytes.length; i++) { if (bytes[i] === 10) { nl = i; break; } }
      line = "";
      for (var j = pos; j < nl; j++) line += String.fromCharCode(bytes[j]);
      if (line.trim().charAt(0) !== "#") tokens = tokens.concat(line.trim().split(/\s+/).filter(Boolean));
      pos = nl + 1;
    }
    if (tokens[0] !== "P5") throw new Error("not a P5 PGM");
    var w = parseInt(tokens[1], 10), h = parseInt(tokens[2], 10);
    return { w: w, h: h, data: bytes.slice(pos, pos + w * h) };
  }
  function serializePgm() {
    var header = "P5\n" + state.w + " " + state.h + "\n255\n";
    var out = new Uint8Array(header.length + state.grid.length);
    for (var i = 0; i < header.length; i++) out[i] = header.charCodeAt(i);
    out.set(state.grid, header.length);
    return out;
  }

  /* ---------------- 渲染 ---------------- */
  function repaintGrid() {
    var img = gtx.createImageData(state.w, state.h);
    for (var i = 0; i < state.grid.length; i++) {
      var v = state.grid[i], o = i * 4;
      img.data[o] = v; img.data[o + 1] = v; img.data[o + 2] = v; img.data[o + 3] = 255;
    }
    gtx.putImageData(img, 0, 0);
  }
  function worldToPx(x, y) {
    return [(x - state.ox) / state.res, state.h - (y - state.oy) / state.res];
  }
  function pxToWorld(px, py) {
    return [state.ox + px * state.res, state.oy + (state.h - py) * state.res];
  }
  function repaintOverlay(robotPose) {
    otx.clearRect(0, 0, state.w, state.h);
    state.points.forEach(function (p, idx) {
      var xy = worldToPx(p.x, p.y), cx = xy[0], cy = xy[1];
      var r = Math.max(3, 6 / state.zoom * 2);
      otx.beginPath(); otx.arc(cx, cy, r, 0, Math.PI * 2);
      otx.fillStyle = idx === state.selected ? "rgba(80,240,180,.95)" : "rgba(80,160,255,.9)";
      otx.fill();
      otx.strokeStyle = "#123"; otx.lineWidth = 1; otx.stroke();
      var ax = Math.cos(p.yaw || 0), ay = -Math.sin(p.yaw || 0);
      otx.beginPath(); otx.moveTo(cx, cy); otx.lineTo(cx + ax * r * 2.2, cy + ay * r * 2.2);
      otx.strokeStyle = idx === state.selected ? "#5fd" : "#8bf"; otx.lineWidth = 2; otx.stroke();
      otx.fillStyle = "#dfe"; otx.font = (r * 2) + "px sans-serif";
      otx.fillText((idx + 1) + " " + (p.name || ""), cx + r + 2, cy - r);
    });
    if (robotPose) {
      var rp = worldToPx(robotPose.x, robotPose.y);
      otx.beginPath(); otx.arc(rp[0], rp[1], 4, 0, Math.PI * 2);
      otx.fillStyle = "rgba(255,120,80,.95)"; otx.fill();
      otx.beginPath(); otx.moveTo(rp[0], rp[1]);
      otx.lineTo(rp[0] + Math.cos(robotPose.yaw) * 10, rp[1] - Math.sin(robotPose.yaw) * 10);
      otx.strokeStyle = "#f97"; otx.lineWidth = 2; otx.stroke();
    }
  }
  function applyZoom() {
    var stage = $("stage");
    stage.style.width = (state.w * state.zoom) + "px";
    stage.style.height = (state.h * state.zoom) + "px";
    gridCanvas.style.width = overlay.style.width = (state.w * state.zoom) + "px";
    gridCanvas.style.height = overlay.style.height = (state.h * state.zoom) + "px";
    $("zoomLabel").textContent = Math.round(state.zoom * 100) + "%";
  }

  /* ---------------- 画笔 ---------------- */
  function stamp(cx, cy) {
    var r = state.penSize, v = state.pen;
    for (var dy = -r; dy <= r; dy++) for (var dx = -r; dx <= r; dx++) {
      if (dx * dx + dy * dy > r * r) continue;
      var x = cx + dx, y = cy + dy;
      if (x >= 0 && x < state.w && y >= 0 && y < state.h) state.grid[y * state.w + x] = v;
    }
  }
  function strokeTo(cell) {
    var a = state.lastCell || cell, steps = Math.max(1, Math.round(Math.hypot(cell[0] - a[0], cell[1] - a[1])));
    for (var s = 0; s <= steps; s++) {
      stamp(Math.round(a[0] + (cell[0] - a[0]) * s / steps), Math.round(a[1] + (cell[1] - a[1]) * s / steps));
    }
    state.lastCell = cell;
  }

  /* ---------------- 保存（实时，防抖） ---------------- */
  var gridTimer = null, pointsTimer = null;
  function scheduleGridSave() {
    state.gridDirty = true; setStatus("未保存…");
    clearTimeout(gridTimer);
    gridTimer = setTimeout(saveGrid, 1200);
  }
  function saveGrid() {
    if (!state.gridDirty) return;
    setStatus("保存栅格中…");
    fetch(API + "/api/core/slam/v1/maps/" + encodeURIComponent(state.mapId) + "/grid", {
      method: "PUT", body: serializePgm(),
      headers: { "Content-Type": "application/octet-stream" },
    }).then(function (r) {
      if (!r.ok) throw new Error("HTTP " + r.status);
      state.gridDirty = false;
      setStatus("✓ 栅格已保存 " + new Date().toLocaleTimeString() + "（重新 :select 后生效）");
    }).catch(function (e) { setStatus("✗ 栅格保存失败: " + e.message); });
  }
  function schedulePointsSave() {
    state.pointsDirty = true; setStatus("未保存…");
    clearTimeout(pointsTimer);
    pointsTimer = setTimeout(savePoints, 1200);
  }
  function savePoints() {
    if (!state.pointsDirty) return;
    setStatus("保存航迹点中…");
    fetch(API + "/api/core/slam/v1/maps/" + encodeURIComponent(state.mapId) + "/tour-points", {
      method: "PUT", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ points: state.points }),
    }).then(function (r) {
      if (!r.ok) throw new Error("HTTP " + r.status);
      state.pointsDirty = false;
      setStatus("✓ 航迹点已保存 " + new Date().toLocaleTimeString());
    }).catch(function (e) { setStatus("✗ 航迹点保存失败: " + e.message); });
  }

  /* ---------------- 航迹点面板 ---------------- */
  function defaultPoint(x, y) {
    return { name: "点" + (state.points.length + 1), x: Math.round(x * 100) / 100, y: Math.round(y * 100) / 100,
             yaw: 0, actions: [], tts_text: "", dwell_s: 0, reach_threshold: 0.35, yaw_threshold: 0.5 };
  }
  function renderPointList() {
    var list = $("pointList"); list.innerHTML = "";
    state.points.forEach(function (p, idx) {
      var row = document.createElement("div");
      row.className = "me-point-row" + (idx === state.selected ? " selected" : "");
      row.textContent = (idx + 1) + ". " + (p.name || "(未命名)") + "  (" + p.x + ", " + p.y + ")";
      row.onclick = function () { selectPoint(idx); };
      list.appendChild(row);
    });
    renderPointForm();
  }
  function selectPoint(idx) { state.selected = idx; renderPointList(); repaintOverlay(lastRobotPose); }
  function renderPointForm() {
    var form = $("pointForm"), p = state.points[state.selected];
    if (!p) { form.style.display = "none"; return; }
    form.style.display = "block";
    $("fName").value = p.name || ""; $("fX").value = p.x; $("fY").value = p.y;
    $("fYaw").value = p.yaw || 0; $("fTts").value = p.tts_text || ""; $("fDwell").value = p.dwell_s || 0;
    $("fTtsDur").value = p.tts_duration_s || 0;
    $("fReach").value = p.reach_threshold != null ? p.reach_threshold : 0.35;
    $("fYawTol").value = p.yaw_threshold != null ? p.yaw_threshold : 0.5;
    renderActionRows(p);
  }
  function bindField(id, key, numeric) {
    $(id).oninput = function () {
      var p = state.points[state.selected]; if (!p) return;
      p[key] = numeric ? parseFloat(this.value || 0) : this.value;
      schedulePointsSave(); repaintOverlay(lastRobotPose);
      if (key === "name") renderPointListOnly();
    };
  }
  function renderPointListOnly() {
    var rows = $("pointList").children;
    state.points.forEach(function (p, idx) {
      if (rows[idx]) rows[idx].textContent = (idx + 1) + ". " + (p.name || "(未命名)") + "  (" + p.x + ", " + p.y + ")";
    });
  }
  function renderActionRows(p) {
    var box = $("actionRows"); box.innerHTML = "";
    (p.actions || []).forEach(function (a, ai) {
      var row = document.createElement("div"); row.className = "me-action-row";
      var executor = a.executor || "onboard";
      var head = document.createElement("div"); head.className = "grid2";
      var exSel = document.createElement("select");
      exSel.innerHTML = '<option value="onboard">机载动作</option><option value="external">外部动作</option>';
      exSel.value = executor;
      var del = document.createElement("button"); del.textContent = "删除"; del.className = "me-btn-danger";
      del.onclick = function () { p.actions.splice(ai, 1); renderActionRows(p); schedulePointsSave(); };
      head.appendChild(exSel); head.appendChild(del); row.appendChild(head);

      var body = document.createElement("div"); row.appendChild(body);
      function renderBody() {
        body.innerHTML = "";
        if ((a.executor || "onboard") === "onboard") {
          var g = document.createElement("div"); g.className = "grid2";
          var typeSel = document.createElement("select");
          typeSel.innerHTML = '<option value="arm">手臂 arm</option><option value="hand">灵巧手 hand</option>';
          typeSel.value = a.type === "hand" ? "hand" : "arm";
          var val = document.createElement("input");
          val.placeholder = typeSel.value === "arm" ? "动作 id（如 26 挥手）" : "指令 cmd（如 7 右手张开）";
          val.value = a.type === "hand" ? (a.cmd || "") : (a.id != null ? a.id : "");
          typeSel.onchange = function () {
            a.type = typeSel.value; delete a.id; delete a.cmd;
            val.value = ""; val.placeholder = a.type === "arm" ? "动作 id（如 26 挥手）" : "指令 cmd（如 7 右手张开）";
            schedulePointsSave();
          };
          val.oninput = function () {
            if (a.type === "hand") { a.cmd = String(val.value); } else { a.type = "arm"; a.id = parseInt(val.value || 0, 10); }
            schedulePointsSave();
          };
          g.appendChild(typeSel); g.appendChild(val); body.appendChild(g);
        } else {
          // 外部动作：优先从回放库选（存成 {type:"replay", id}），自定义兜底
          var candidate = a.id || (a.type && a.type !== "replay" ? a.type : "");
          var inLib = state.extLib.some(function (x) { return x.action_id === candidate; });
          var libSel = document.createElement("select");
          var opts = '<option value="">— 回放库动作 —</option>';
          state.extLib.forEach(function (x) {
            opts += '<option value="' + x.action_id + '">' + x.action_id +
                    "（" + (x.duration_s || 0) + "s）</option>";
          });
          opts += '<option value="__custom__">自定义（非回放执行器）</option>';
          libSel.innerHTML = opts;
          libSel.value = inLib ? candidate : (candidate ? "__custom__" : "");
          var custom = document.createElement("div");
          function renderCustom() {
            custom.innerHTML = "";
            if (libSel.value !== "__custom__") return;
            var name = document.createElement("input");
            name.placeholder = "外部动作名（自定义执行器识别用）"; name.value = a.type || "";
            name.oninput = function () { a.type = name.value; delete a.id; schedulePointsSave(); };
            var params = document.createElement("input");
            params.placeholder = '参数 JSON（可空，如 {"speed":1}）';
            params.value = a.params ? JSON.stringify(a.params) : "";
            params.oninput = function () {
              try { a.params = params.value ? JSON.parse(params.value) : undefined; params.style.borderColor = ""; schedulePointsSave(); }
              catch (e) { params.style.borderColor = "#f66"; }
            };
            custom.appendChild(name); custom.appendChild(params);
          }
          libSel.onchange = function () {
            if (libSel.value && libSel.value !== "__custom__") {
              a.type = "replay"; a.id = libSel.value; delete a.params; delete a.cmd;
            } else if (libSel.value === "__custom__") {
              delete a.id; a.type = "";
            }
            renderCustom(); schedulePointsSave();
          };
          var hint = document.createElement("div"); hint.className = "me-hint";
          hint.textContent = state.extLib.length
            ? "回放库动作到点自动执行（需执行器窗格在跑）；库为空时先在 /sdk「外部动作」上传。"
            : "回放库为空：先在 /sdk「外部动作」组上传 .npy；自定义类型走 tour.external_executor_url 透传。";
          body.appendChild(libSel); body.appendChild(custom); body.appendChild(hint);
          renderCustom();
        }
      }
      exSel.onchange = function () {
        a.executor = exSel.value;
        if (a.executor === "onboard") { delete a.params; a.type = "arm"; delete a.cmd; }
        else { delete a.id; delete a.cmd; a.type = a.type === "arm" || a.type === "hand" ? "" : (a.type || ""); }
        renderBody(); schedulePointsSave();
      };
      renderBody();
      box.appendChild(row);
    });
  }

  /* ---------------- 指针交互 ---------------- */
  function eventCell(e) {
    var rect = overlay.getBoundingClientRect();
    var px = (e.clientX - rect.left) / state.zoom, py = (e.clientY - rect.top) / state.zoom;
    return [Math.floor(px), Math.floor(py)];
  }
  function hitPoint(e) {
    var rect = overlay.getBoundingClientRect();
    var px = (e.clientX - rect.left) / state.zoom, py = (e.clientY - rect.top) / state.zoom;
    for (var i = state.points.length - 1; i >= 0; i--) {
      var xy = worldToPx(state.points[i].x, state.points[i].y);
      if (Math.hypot(px - xy[0], py - xy[1]) < 8 / state.zoom * 2 + 3) return i;
    }
    return -1;
  }
  overlay.style.touchAction = "none";
  overlay.addEventListener("pointerdown", function (e) {
    if (!state.grid) return;
    overlay.setPointerCapture(e.pointerId);
    if (state.mode === "draw") {
      state.undoStack.push(state.grid.slice());
      if (state.undoStack.length > 30) state.undoStack.shift();
      state.drawing = true; state.lastCell = null;
      strokeTo(eventCell(e)); repaintGrid();
    } else {
      var hit = hitPoint(e);
      if (hit >= 0) { selectPoint(hit); state.dragging = true; }
      else {
        var cell = eventCell(e), wxy = pxToWorld(cell[0] + 0.5, cell[1] + 0.5);
        state.points.push(defaultPoint(wxy[0], wxy[1]));
        selectPoint(state.points.length - 1);
        schedulePointsSave();
      }
    }
  });
  overlay.addEventListener("pointermove", function (e) {
    if (state.mode === "draw" && state.drawing) { strokeTo(eventCell(e)); repaintGrid(); }
    else if (state.mode === "points" && state.dragging && state.selected >= 0) {
      var cell = eventCell(e), wxy = pxToWorld(cell[0] + 0.5, cell[1] + 0.5);
      var p = state.points[state.selected];
      p.x = Math.round(wxy[0] * 100) / 100; p.y = Math.round(wxy[1] * 100) / 100;
      $("fX").value = p.x; $("fY").value = p.y;
      renderPointListOnly(); repaintOverlay(lastRobotPose);
    }
  });
  overlay.addEventListener("pointerup", function () {
    if (state.drawing) { state.drawing = false; scheduleGridSave(); }
    if (state.dragging) { state.dragging = false; schedulePointsSave(); }
  });

  /* ---------------- 工具栏 ---------------- */
  function setMode(mode) {
    state.mode = mode;
    $("modeDraw").classList.toggle("active", mode === "draw");
    $("modePoints").classList.toggle("active", mode === "points");
    $("drawTools").style.display = mode === "draw" ? "" : "none";
    overlay.style.cursor = mode === "draw" ? "crosshair" : "pointer";
  }
  $("modeDraw").onclick = function () { setMode("draw"); };
  $("modePoints").onclick = function () { setMode("points"); };
  function setPen(v, btn) {
    state.pen = v;
    ["penObstacle", "penFree", "penUnknown"].forEach(function (id) { $(id).classList.remove("active"); });
    btn.classList.add("active");
  }
  $("penObstacle").onclick = function () { setPen(0, this); };
  $("penFree").onclick = function () { setPen(254, this); };
  $("penUnknown").onclick = function () { setPen(205, this); };
  $("penSize").onchange = function () { state.penSize = parseInt(this.value, 10); };
  $("undoBtn").onclick = function () {
    var prev = state.undoStack.pop();
    if (prev) { state.grid = prev; repaintGrid(); scheduleGridSave(); }
  };
  $("zoomIn").onclick = function () { state.zoom = Math.min(16, state.zoom * 1.5); applyZoom(); };
  $("zoomOut").onclick = function () { state.zoom = Math.max(0.5, state.zoom / 1.5); applyZoom(); };
  $("canvasWrap").addEventListener("wheel", function (e) {
    if (!e.ctrlKey) return;
    e.preventDefault();
    state.zoom = Math.min(16, Math.max(0.5, state.zoom * (e.deltaY < 0 ? 1.2 : 1 / 1.2)));
    applyZoom();
  }, { passive: false });
  $("addAction").onclick = function () {
    var p = state.points[state.selected]; if (!p) return;
    p.actions = p.actions || [];
    p.actions.push({ executor: "onboard", type: "arm", id: 26 });
    renderActionRows(p); schedulePointsSave();
  };
  $("delPoint").onclick = function () {
    if (state.selected < 0) return;
    state.points.splice(state.selected, 1);
    state.selected = -1;
    renderPointList(); repaintOverlay(lastRobotPose); schedulePointsSave();
  };
  /* 试跑单点：先 flush 保存航迹点，再发单点试跑（单点导览），轮询到终态。
     nav=false 为原地排练（不走动，只动作+TTS+停留）。 */
  function runPointTest(nav) {
    if (state.selected < 0) return;
    var idx = state.selected;
    clearTimeout(pointsTimer);
    var pre = state.pointsDirty ? (savePoints(), new Promise(function (res) { setTimeout(res, 600); }))
                                : Promise.resolve();
    setStatus("▶ 试跑点 " + (idx + 1) + (nav ? " …（走过去+动作+TTS）" : " …（原地，只动作+TTS）"));
    pre.then(function () {
      return fetch(API + "/api/tour/v1/points/" + idx + "/:test" + (nav ? "?nav=1" : ""),
                   { method: "POST" });
    }).then(function (r) {
      if (!r.ok) return r.text().then(function (t) { throw new Error("HTTP " + r.status + " " + t.slice(0, 120)); });
      var timer = setInterval(function () {
        fetch(API + "/api/tour/v1/tours/:current").then(function (rr) { return rr.json(); })
          .then(function (st) {
            if (["finished", "failed", "stopped"].indexOf(st.status) >= 0) {
              clearInterval(timer);
              setStatus((st.status === "finished" ? "✓ 试跑完成：点 " : "✗ 试跑" + st.status + "：点 ") + (idx + 1));
            } else {
              setStatus("▶ 试跑中（" + st.status + "）… /tours/:stop 可中止");
            }
          }).catch(function () { clearInterval(timer); });
      }, 1500);
    }).catch(function (e) { setStatus("✗ 试跑失败: " + e.message); });
  }
  $("testPoint").onclick = function () { runPointTest(true); };
  $("testPointInPlace").onclick = function () { runPointTest(false); };
  ["fName", "fTts"].forEach(function (id) { });
  bindField("fName", "name", false); bindField("fX", "x", true); bindField("fY", "y", true);
  bindField("fYaw", "yaw", true); bindField("fTts", "tts_text", false); bindField("fDwell", "dwell_s", true);
  bindField("fTtsDur", "tts_duration_s", true);
  bindField("fReach", "reach_threshold", true); bindField("fYawTol", "yaw_threshold", true);

  /* 保存并应用：flush 保存 → 记当前位姿 → :select → 若在此图上已定位则原位重定位 */
  $("applyBtn").onclick = function () {
    clearTimeout(gridTimer); clearTimeout(pointsTimer);
    var chain = Promise.resolve();
    if (state.gridDirty) chain = chain.then(function () { return new Promise(function (res) { saveGrid(); setTimeout(res, 400); }); });
    if (state.pointsDirty) chain = chain.then(function () { return new Promise(function (res) { savePoints(); setTimeout(res, 400); }); });
    var pose = null;
    chain.then(function () {
      if (state.mapId !== state.currentMapId) return null;
      return fetch(API + "/api/core/slam/v1/localization/status").then(function (r) { return r.json(); })
        .then(function (st) { if (st.initialized && st.pose) pose = { x: st.pose.x_m, y: st.pose.y_m, yaw: st.pose.yaw_rad }; });
    }).then(function () {
      setStatus("重新选图中…");
      return fetch(API + "/api/core/slam/v1/maps/" + encodeURIComponent(state.mapId) + "/:select", { method: "POST" });
    }).then(function (r) {
      if (!r.ok) throw new Error("select HTTP " + r.status);
      state.currentMapId = state.mapId;
      if (!pose) { setStatus("✓ 已应用（此图未在定位中，使用前先重定位）"); return null; }
      setStatus("原位重定位中…");
      return fetch(API + "/api/core/slam/v1/localization/:relocalize", {
        method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(pose),
      }).then(function (rr) {
        if (!rr.ok) throw new Error("relocalize HTTP " + rr.status);
        setStatus("✓ 已应用：导航栈加载新图，定位保持");
      });
    }).catch(function (e) { setStatus("✗ 应用失败: " + e.message); });
  };

  /* ---------------- 机器人位姿轮询 ---------------- */
  var lastRobotPose = null;
  setInterval(function () {
    if (!state.mapId || state.mapId !== state.currentMapId) { lastRobotPose = null; return; }
    fetch(API + "/api/core/slam/v1/localization/pose").then(function (r) {
      if (!r.ok) throw 0;
      return r.json();
    }).then(function (p) { lastRobotPose = p; repaintOverlay(lastRobotPose); })
      .catch(function () { lastRobotPose = null; });
  }, 2000);

  /* ---------------- 加载 ---------------- */
  function loadMap(mapId, manifest) {
    state.mapId = mapId;
    state.res = manifest.resolution || 0.05;
    state.ox = (manifest.origin || [0, 0, 0])[0]; state.oy = (manifest.origin || [0, 0, 0])[1];
    state.points = (manifest.tour_points || []).map(function (p) { return JSON.parse(JSON.stringify(p)); });
    state.selected = -1; state.undoStack = []; state.gridDirty = false; state.pointsDirty = false;
    setStatus("加载栅格…");
    fetch(API + "/api/core/slam/v1/maps/" + encodeURIComponent(mapId) + "/grid")
      .then(function (r) { if (!r.ok) throw new Error("HTTP " + r.status); return r.arrayBuffer(); })
      .then(function (buf) {
        var pgm = parsePgm(buf);
        state.w = pgm.w; state.h = pgm.h; state.grid = pgm.data;
        gridCanvas.width = overlay.width = pgm.w;
        gridCanvas.height = overlay.height = pgm.h;
        state.zoom = Math.max(1, Math.min(8, Math.floor(700 / pgm.h)));
        applyZoom(); repaintGrid(); repaintOverlay(null); renderPointList();
        setStatus("✓ 已加载 " + mapId + "（" + pgm.w + "×" + pgm.h + " @ " + state.res + "m）");
      })
      .catch(function (e) { setStatus("✗ 加载失败: " + e.message); });
  }

  function init() {
    Promise.all([
      fetch(API + "/api/core/slam/v1/maps").then(function (r) { return r.json(); }),
      fetch(API + "/api/core/slam/v1/maps/:current").then(function (r) { return r.json(); }),
      fetch(API + "/api/core/motion/v1/external-actions").then(function (r) { return r.json(); })
        .catch(function () { return { actions: [] }; }),
    ]).then(function (rs) {
      var maps = rs[0].maps || [];
      state.extLib = (rs[2] && rs[2].actions) || [];
      state.currentMapId = rs[1].map ? rs[1].map.map_id : null;
      var sel = $("mapSel"); sel.innerHTML = "";
      if (!maps.length) { setStatus("地图库为空：先建图或上传 .g1map"); return; }
      maps.forEach(function (m) {
        var opt = document.createElement("option");
        opt.value = m.map_id;
        opt.textContent = m.map_id + (m.map_id === state.currentMapId ? "（当前）" : "");
        sel.appendChild(opt);
      });
      var manifestById = {};
      maps.forEach(function (m) { manifestById[m.map_id] = m; });
      sel.onchange = function () { loadMap(sel.value, manifestById[sel.value]); };
      var first = state.currentMapId && manifestById[state.currentMapId] ? state.currentMapId : maps[0].map_id;
      sel.value = first;
      loadMap(first, manifestById[first]);
    }).catch(function (e) { setStatus("✗ 初始化失败: " + e.message); });
  }
  setMode("draw");
  init();
})();
