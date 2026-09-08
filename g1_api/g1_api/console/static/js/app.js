/* G1 SDK debug console: renders the meta.json catalog as an accordion,
 * executes endpoints with a pre-filled sample, and auto-polls actions / tours.
 * Pure ES2017, no external dependencies. */
(function () {
  "use strict";

  var state = { meta: null };

  var SAFETY_LABEL = {
    SAFE_READ: "只读",
    ACTION: "动作",
    SAFETY_CRITICAL: "安全关键",
  };

  function el(tag, cls, text) {
    var node = document.createElement(tag);
    if (cls) node.className = cls;
    if (text !== undefined && text !== null) node.textContent = text;
    return node;
  }

  function esc(s) {
    return String(s == null ? "" : s)
      .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
  }

  function fmtJson(v) {
    return JSON.stringify(v, null, 2);
  }

  function renderTopbar(meta) {
    document.getElementById("mode-badge").textContent = meta.mode === "real" ? "real" : "mock";
    document.getElementById("mode-badge").className = "badge " + (meta.mode === "real" ? "real" : "mock");
    var flags = meta.safety_flags || {};
    var on = Object.keys(flags).filter(function (k) { return flags[k]; });
    document.getElementById("safety-summary").textContent =
      "安全开关：" + (on.length ? on.join(", ") : "全部关闭（默认）");
  }

  function renderApp(meta) {
    var app = document.getElementById("app");
    app.innerHTML = "";
    meta.groups.forEach(function (group) {
      var eps = meta.endpoints.filter(function (e) { return e.group === group.id; });
      if (!eps.length) return;
      var gwrap = el("section", "group");
      gwrap.appendChild(el("div", "group-head", group.name));
      eps.forEach(function (ep) { gwrap.appendChild(renderEndpoint(ep)); });
      app.appendChild(gwrap);
    });
  }

  function renderEndpoint(ep) {
    var wrap = el("div", "ep");
    var head = el("div", "ep-head");
    head.appendChild(el("span", "method " + ep.method, ep.method));
    head.appendChild(el("span", "path", ep.path));
    head.appendChild(el("span", "title", ep.title));
    var badge = el("span", "badge " + ep.safety, SAFETY_LABEL[ep.safety] || ep.safety);
    head.appendChild(badge);
    head.appendChild(el("span", "caret", "▶"));

    var body = el("div", "ep-body");
    body.style.display = "none";
    buildBody(ep, body);

    head.addEventListener("click", function () {
      var open = body.style.display !== "none";
      body.style.display = open ? "none" : "block";
      wrap.classList.toggle("open", !open);
    });
    wrap.appendChild(head);
    wrap.appendChild(body);
    return wrap;
  }

  function buildBody(ep, body) {
    // ① 中文说明
    body.appendChild(el("h4", null, "说明"));
    body.appendChild(el("p", "desc", ep.description));

    // ② 参数表
    if (ep.params && ep.params.length) {
      body.appendChild(el("h4", null, "参数"));
      var table = el("table", "params");
      var thead = document.createElement("thead");
      var hr = document.createElement("tr");
      ["名称", "类型", "单位", "必填", "含义"].forEach(function (h) {
        hr.appendChild(el("th", null, h));
      });
      thead.appendChild(hr);
      table.appendChild(thead);
      var tbody = document.createElement("tbody");
      ep.params.forEach(function (p) {
        var tr = document.createElement("tr");
        tr.appendChild(el("td", "mono", p.name));
        tr.appendChild(el("td", "mono", p.type));
        tr.appendChild(el("td", null, p.unit || ""));
        tr.appendChild(el("td", null, p.required ? "是" : "否"));
        tr.appendChild(el("td", null, p.desc || ""));
        tbody.appendChild(tr);
      });
      table.appendChild(tbody);
      body.appendChild(table);
    }

    // ③ 预填样例 (editable)
    var pathInputs = buildPathInputs(ep.path);
    var sampleBox = null;
    var needsBody = (ep.method === "POST" || ep.method === "PUT") && ep.sample !== null && ep.sample !== undefined;
    if (needsBody) {
      body.appendChild(el("h4", null, "样例（可直接运行，可编辑）"));
      sampleBox = el("textarea", "sample");
      sampleBox.value = fmtJson(ep.sample);
      body.appendChild(sampleBox);
    }

    // ③b 文件上传模式（meta 带 ep.upload 的端点：body=文件字节流 + query 参数）
    var uploadFile = null, uploadInputs = [];
    if (ep.upload) {
      body.appendChild(el("h4", null, "上传（选文件 + 参数，点 Execute）"));
      var upRow = el("div", "row");
      var fileInput = document.createElement("input");
      fileInput.type = "file";
      if (ep.upload.accept) fileInput.accept = ep.upload.accept;
      fileInput.addEventListener("change", function () {
        uploadFile = fileInput.files[0] || null;
        // name 默认取文件名（去扩展名），用户改过就不再覆盖
        var nameInp = uploadInputs.filter(function (i) { return i.dataset.name === "name"; })[0];
        if (uploadFile && nameInp && !nameInp.dataset.touched) {
          nameInp.value = uploadFile.name.replace(/\.[^.]+$/, "");
        }
      });
      upRow.appendChild(fileInput);
      (ep.upload.query || []).forEach(function (q) {
        var input = document.createElement("input");
        input.type = "text";
        input.dataset.name = q;
        input.placeholder = q;
        input.className = "sample small";
        input.style.cssText = "width:110px;";
        if (q === "frequency") input.value = "30";
        if (q === "velocity_limit") input.value = "20";
        input.addEventListener("input", function () { input.dataset.touched = "1"; });
        var label = document.createElement("label");
        label.className = "small muted";
        label.appendChild(document.createTextNode(q + ": "));
        label.appendChild(input);
        upRow.appendChild(label);
        uploadInputs.push(input);
      });
      body.appendChild(upRow);
    }

    function uploadQuery() {
      var parts = [];
      uploadInputs.forEach(function (i) {
        if (i.value) parts.push(encodeURIComponent(i.dataset.name) + "=" + encodeURIComponent(i.value));
      });
      return parts.length ? "?" + parts.join("&") : "";
    }

    // ④ Execute + response
    body.appendChild(el("h4", null, "执行"));
    var controls = el("div", "row");
    pathInputs.forEach(function (pi) { controls.appendChild(pi); });
    var runBtn = el("button", null, "Execute");
    controls.appendChild(runBtn);
    if (needsBody) {
      var resetBtn = el("button", "secondary", "恢复样例");
      resetBtn.addEventListener("click", function () { sampleBox.value = fmtJson(ep.sample); });
      controls.appendChild(resetBtn);
    }
    var curlBtn = el("button", "secondary", "复制 curl");
    controls.appendChild(curlBtn);
    body.appendChild(controls);

    var resp = el("div", "resp");
    body.appendChild(resp);

    function resolvePath() {
      var out = ep.path;
      pathInputs.forEach(function (pi) {
        // buildPathInputs returns <label> wrappers; the input lives inside.
        var inp = pi.querySelector ? pi.querySelector("input") : pi;
        out = out.replace("{" + inp.dataset.name + "}", encodeURIComponent(inp.value || ""));
      });
      return out;
    }

    function currentBody() {
      if (!needsBody) return null;
      return sampleBox.value;
    }

    function curl() {
      if (ep.upload) {
        return ["curl", "-X", ep.method,
                "'" + location.origin + resolvePath() + uploadQuery() + "'",
                "--data-binary", "@" + (uploadFile ? uploadFile.name : "<文件>"),
                "-H 'Content-Type: application/octet-stream'"].join(" ");
      }
      var parts = ["curl", "-sS", "-X", ep.method, "'" + location.origin + resolvePath() + "'"];
      if (needsBody) {
        parts.push("-H 'Content-Type: application/json'");
        parts.push("-d '" + sampleBox.value.replace(/'/g, "'\\''") + "'");
      }
      return parts.join(" ");
    }

    curlBtn.addEventListener("click", function () {
      var ta = document.createElement("textarea");
      ta.value = curl();
      document.body.appendChild(ta);
      ta.select();
      try { document.execCommand("copy"); } catch (e) {}
      document.body.removeChild(ta);
      curlBtn.textContent = "已复制";
      setTimeout(function () { curlBtn.textContent = "复制 curl"; }, 1200);
    });

    runBtn.addEventListener("click", function () {
      var started = performance.now();
      resp.innerHTML = "";
      if (ep.upload) {
        if (!uploadFile) {
          renderResult(resp, null, { statusText: "先选择文件" }, started);
          return;
        }
        uploadFile.arrayBuffer().then(function (buf) {
          return fetch(resolvePath() + uploadQuery(), {
            method: ep.method,
            headers: { "Content-Type": "application/octet-stream" },
            body: buf,
          });
        }).then(function (r) {
          return r.text().then(function (t) {
            var data;
            try { data = JSON.parse(t); } catch (e) { data = t; }
            renderResult(resp, r, data, started);
          });
        }).catch(function (err) {
          renderResult(resp, null, { statusText: err.message }, started);
        });
        return;
      }
      var url = resolvePath();
      var opts = { method: ep.method, headers: {} };
      if (needsBody) {
        opts.headers["Content-Type"] = "application/json";
        try { opts.body = JSON.stringify(JSON.parse(sampleBox.value)); }
        catch (e) {
          renderResult(resp, null, { statusText: "样例不是合法 JSON: " + e.message }, started);
          return;
        }
      }
      fetch(url, opts).then(function (r) {
        return r.text().then(function (t) {
          var data;
          try { data = JSON.parse(t); } catch (e) { data = t; }
          renderResult(resp, r, data, started);
          maybePoll(ep, r, data, resp, started);
        });
      }).catch(function (err) {
        renderResult(resp, null, { statusText: err.message }, started);
      });
    });
  }

  function buildPathInputs(path) {
    var inputs = [];
    var re = /\{([^}]+)\}/g;
    var m;
    while ((m = re.exec(path)) !== null) {
      var name = m[1];
      var input = document.createElement("input");
      input.type = "text";
      input.dataset.name = name;
      input.placeholder = name;
      input.className = "sample small";
      input.style.cssText = "width:110px;";
      // sensible defaults
      if (name === "action_id") input.value = "1";
      if (name === "map_id") input.value = "";
      var label = document.createElement("label");
      label.className = "small muted";
      label.appendChild(document.createTextNode(name + ": "));
      label.appendChild(input);
      inputs.push(label);
    }
    return inputs;
  }

  function renderResult(resp, httpResp, data, started) {
    var ms = Math.round(performance.now() - started);
    var line = el("div", "status");
    if (httpResp) {
      var ok = httpResp.status >= 200 && httpResp.status < 300;
      line.textContent = httpResp.status + " · " + ms + " ms";
      line.className = "status " + (ok ? "ok" : "err");
    } else {
      line.textContent = "请求失败 · " + ms + " ms";
      line.className = "status err";
    }
    resp.appendChild(line);

    // interlock rendering (409-style responses)
    var interlocks = null;
    if (data && typeof data === "object") {
      interlocks = data.interlocks || (data.details && (data.details.active_interlocks || data.details.interlocks));
    }
    if (httpResp && httpResp.status === 409 && interlocks) {
      resp.appendChild(renderInterlocks(interlocks));
    }

    var pre = el("pre", "output", fmtJson(data));
    resp.appendChild(pre);
  }

  function renderInterlocks(interlocks) {
    var box = el("div", "interlocks");
    box.appendChild(el("div", "small", "命名互锁（附解除方法）："));
    var remedies = {
      roscore_down: "启动 ROS master（roscore）。",
      move_base_down: "确认 navigation.launch 已启动、/move_base 服务就绪。",
      velocity_bridge_down: "启动 g1_control_vel.py <iface>（/cmd_vel 桥）。",
      dds_unreachable: "确认 G1_NETWORK_INTERFACE 正确、DDS 通道可达。",
      map_not_selected: "先 POST /maps/{id}/:select 选择地图。",
      localization_not_initialized: "先 POST /localization/:relocalize 重定位。",
      mapping_running: "建图会话进行中：先 POST /mapping/:finish 入库或 /mapping/:cancel 放弃。",
      tour_running: "等待导览结束或 POST /tours/:stop。",
      motion_not_allowed: "设置 G1_API_SAFETY_ALLOW_MOTION=true。",
      navigation_not_allowed: "设置 G1_API_SAFETY_ALLOW_NAVIGATION=true。",
      map_write_not_allowed: "设置 G1_API_SAFETY_ALLOW_MAP_WRITE=true。",
      arm_not_allowed: "设置 G1_API_SAFETY_ALLOW_ARM=true。",
      hand_not_allowed: "设置 G1_API_SAFETY_ALLOW_HAND=true。",
      voice_not_allowed: "设置 G1_API_SAFETY_ALLOW_VOICE=true。",
      tour_not_allowed: "设置 G1_API_SAFETY_ALLOW_TOUR=true。",
    };
    var ul = document.createElement("ul");
    (Array.isArray(interlocks) ? interlocks : [interlocks]).forEach(function (name) {
      var li = document.createElement("li");
      li.appendChild(el("code", null, name));
      li.appendChild(document.createTextNode(" — " + (remedies[name] || "见文档 /docs/DEPLOY_PC2.md")));
      ul.appendChild(li);
    });
    box.appendChild(ul);
    return box;
  }

  function maybePoll(ep, httpResp, data, resp, started) {
    if (!ep.poll) return;
    if (ep.poll === "action") {
      // After a successful POST /actions, poll /actions/{id}.
      if (httpResp && httpResp.status === 200 && data && typeof data.action_id === "number") {
        var poller = el("div", "poll-line");
        resp.appendChild(poller);
        pollAction(data.action_id, poller, started);
      }
    } else if (ep.poll === "tour") {
      var tourPoller = el("div", "poll-line");
      resp.appendChild(tourPoller);
      pollTour(tourPoller);
    }
  }

  function pollAction(actionId, node, started) {
    var ticks = 0;
    (function tick() {
      fetch("/api/core/motion/v1/actions/" + actionId).then(function (r) {
        return r.json().then(function (d) {
          var st = d.state || {};
          node.textContent = "[轮询] action " + actionId + " · stage=" + (d.stage || "?") +
            " · status=" + st.status + " result=" + st.result;
          ticks += 1;
          if (st.status === 4 || ticks > 200) {
            node.textContent += " · 终态 (" + Math.round(performance.now() - started) + " ms)";
            return;
          }
          setTimeout(tick, 300);
        });
      }).catch(function () { node.textContent = "[轮询] 查询动作失败"; });
    })();
  }

  function pollTour(node) {
    var ticks = 0;
    (function tick() {
      fetch("/api/tour/v1/tours/:current").then(function (r) {
        return r.json().then(function (d) {
          node.textContent = "[轮询] 导览 status=" + (d.status || "?") +
            " · 当前点=" + (d.current_point_index != null ? d.current_point_index : "-");
          ticks += 1;
          if (d.status === "finished" || d.status === "failed" || d.status === "stopped" || ticks > 400) return;
          setTimeout(tick, 500);
        });
      }).catch(function () { node.textContent = "[轮询] 查询导览失败"; });
    })();
  }

  function boot() {
    fetch("/sdk/meta.json").then(function (r) { return r.json(); }).then(function (meta) {
      state.meta = meta;
      renderTopbar(meta);
      renderApp(meta);
    }).catch(function (err) {
      document.getElementById("app").innerHTML =
        '<p class="loading">加载 meta.json 失败: ' + esc(err.message) + "</p>";
    });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", boot);
  } else {
    boot();
  }
})();
