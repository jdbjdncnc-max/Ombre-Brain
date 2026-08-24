/* claude-ui.js — 只做 DOM 组织与视觉行为，不改任何现有元素的 id/class/data-*，
   不解绑任何事件；所有保存/导入等动作都转发给原有按钮。 */
(function () {
  "use strict";

  var SVG = "http://www.w3.org/2000/svg";

  function svg(viewBox, paths, cls) {
    var node = document.createElementNS(SVG, "svg");
    node.setAttribute("viewBox", viewBox);
    node.setAttribute("aria-hidden", "true");
    if (cls) node.setAttribute("class", cls);
    paths.forEach(function (d) {
      var p = document.createElementNS(SVG, "path");
      p.setAttribute("d", d);
      node.appendChild(p);
    });
    return node;
  }

  function el(tag, cls, text) {
    var node = document.createElement(tag);
    if (cls) node.className = cls;
    if (text != null) node.textContent = text;
    return node;
  }

  /* ---------- 1. 只记录键盘开关状态，不逐帧移动玻璃层 ---------- */

  function keyboardStateWatch() {
    var root = document.documentElement;
    var baseHeight = window.innerHeight;
    var lastOpen = false;
    var raf = 0;

    function isComposerFocused() {
      return document.activeElement === document.querySelector("#messageInput");
    }

    function apply() {
      raf = 0;
      var open = isComposerFocused() && baseHeight - window.innerHeight > 120;
      if (open === lastOpen) return;
      lastOpen = open;
      root.classList.toggle("ct-keyboard-open", open);
    }

    function schedule() {
      if (raf) return;
      raf = window.requestAnimationFrame(apply);
    }

    window.addEventListener("resize", schedule, { passive: true });
    window.addEventListener("orientationchange", function () {
      baseHeight = window.innerHeight;
      schedule();
    }, { passive: true });
    document.addEventListener("focusin", schedule, true);
    document.addEventListener("focusout", schedule, true);
  }

  /* ---------- 2. 加载动画：被不断描绘的无限符号 ---------- */

  var INFINITY_D =
    "M6,15 C6,7 12,7 20,15 C28,23 34,23 34,15 C34,7 28,7 20,15 C12,23 6,23 6,15 Z";

  function buildLoader() {
    var shell = document.querySelector(".composer-shell");
    if (!shell || shell.querySelector(".ct-loader")) return null;
    var loader = el("div", "ct-loader");
    loader.setAttribute("aria-hidden", "true");
    var mark = svg("0 0 40 30", [INFINITY_D, INFINITY_D], "ct-infinity");
    mark.childNodes[0].setAttribute("class", "ct-inf-ghost");
    mark.childNodes[1].setAttribute("class", "ct-inf-draw");
    mark.childNodes[1].setAttribute("pathLength", "200");
    loader.appendChild(mark);
    shell.appendChild(loader);
    return loader;
  }

  /* ---------- 3. 缓存信息收进按钮 ---------- */

  var BUCKET = [
    "M4 7c0-1.7 3.6-3 8-3s8 1.3 8 3-3.6 3-8 3-8-1.3-8-3z",
    "M4 7v10c0 1.7 3.6 3 8 3s8-1.3 8-3V7",
    "M4 12c0 1.7 3.6 3 8 3s8-1.3 8-3"
  ];

  function decorateUsage(footer) {
    if (footer.dataset.ctUsage === "1") return;
    var usage = footer.querySelector(".message-usage");
    var actions = footer.querySelector(".message-actions");
    if (!usage || !actions) return;
    footer.dataset.ctUsage = "1";

    var pop = el("div", "ct-usage-pop");
    pop.hidden = true;
    pop.appendChild(usage);

    var btn = el("button", "message-action-button ct-usage-btn");
    btn.type = "button";
    btn.title = "查看缓存与 token 用量";
    btn.setAttribute("aria-label", "查看缓存与 token 用量");
    btn.setAttribute("aria-expanded", "false");
    btn.appendChild(svg("0 0 24 24", BUCKET));
    btn.addEventListener("click", function () {
      var open = pop.hidden;
      pop.hidden = !open;
      btn.setAttribute("aria-expanded", open ? "true" : "false");
    });
    actions.insertBefore(btn, actions.firstChild);

    var body = footer.parentNode;
    if (body) body.insertBefore(pop, footer.nextSibling);
  }

  function watchMessages() {
    var list = document.querySelector("#messageList");
    if (!list) return;
    var loader = buildLoader();

    function sweep() {
      var footers = list.querySelectorAll(".message.assistant .message-footer");
      for (var i = 0; i < footers.length; i += 1) decorateUsage(footers[i]);
      if (loader) {
        loader.classList.toggle(
          "is-active",
          Boolean(list.querySelector(".message.assistant.pending"))
        );
      }
    }

    new MutationObserver(sweep).observe(list, { childList: true, subtree: true });
    sweep();
  }

  /* ---------- 4. MCP：导入 与 已有服务 分开 ---------- */

  function splitMcp() {
    var manager = document.querySelector("#mcpManager");
    var form = document.querySelector("#mcpImportForm");
    if (!manager || !form || manager.querySelector(".ct-mcp-switch")) return;

    var wrap = el("div", "ct-mcp-switch");
    wrap.setAttribute("role", "tablist");
    var tabs = [
      { key: "ct-mcp-list", label: "已有服务" },
      { key: "ct-mcp-import", label: "导入配置" }
    ].map(function (item) {
      var b = el("button", null, item.label);
      b.type = "button";
      b.setAttribute("role", "tab");
      b.addEventListener("click", function () {
        show(item.key);
      });
      wrap.appendChild(b);
      return { key: item.key, node: b };
    });

    function show(key) {
      manager.classList.remove("ct-mcp-list", "ct-mcp-import");
      manager.classList.add(key);
      tabs.forEach(function (t) {
        t.node.setAttribute("aria-selected", t.key === key ? "true" : "false");
      });
    }

    manager.insertBefore(wrap, form);
    show("ct-mcp-list");
  }

  /* ---------- 5. 设置页：先分类，点进去才是具体设置 ---------- */

  var CAT_ICONS = {
    identity: ["M12 12a4 4 0 1 0 0-8 4 4 0 0 0 0 8z", "M4.5 20a7.5 7.5 0 0 1 15 0"],
    link: ["M9 15l6-6", "M11 6l1-1a4 4 0 0 1 6 6l-1 1", "M13 18l-1 1a4 4 0 0 1-6-6l1-1"],
    model: [
      "M12 3v3M12 18v3M3 12h3M18 12h3M5.6 5.6l2.1 2.1M16.3 16.3l2.1 2.1M18.4 5.6l-2.1 2.1M7.7 16.3l-2.1 2.1",
      "M12 8.5a3.5 3.5 0 1 0 0 7 3.5 3.5 0 0 0 0-7z"
    ],
    look: ["M4 6h16v12H4z", "M7 15l3-3 2 2 3-4 2 5", "M8.5 9.5h.01"],
    data: [
      "M4 6c0-1.4 3.6-2.5 8-2.5S20 4.6 20 6s-3.6 2.5-8 2.5S4 7.4 4 6z",
      "M4 6v12c0 1.4 3.6 2.5 8 2.5s8-1.1 8-2.5V6",
      "M4 12c0 1.4 3.6 2.5 8 2.5s8-1.1 8-2.5"
    ]
  };

  var CATEGORIES = [
    {
      id: "identity",
      title: "身份与模型",
      hint: "头像、名称、模型型号",
      picks: [".identity-panel"]
    },
    {
      id: "link",
      title: "连接",
      hint: "后端地址、Duetto、网关令牌",
      picks: ["#backendUrl", "#duettoUrl", "#gatewayToken"]
    },
    {
      id: "model",
      title: "模型与提示词",
      hint: "温度、系统提示词、总结与思考覆写",
      picks: ["#temperature", ".prompt-file-field", "#summaryModel", "#reasoningPresentationPrompt"]
    },
    {
      id: "look",
      title: "外观",
      hint: "主题色、背景图与透明度",
      picks: [
        "#accentColor",
        "#backgroundUrl",
        "#chooseBackgroundButton",
        "#backgroundTransparency",
        "#backgroundFit"
      ]
    },
    {
      id: "data",
      title: "数据与备份",
      hint: "导入导出聊天记录、清空对话",
      picks: ["#importChatMode", "#saveSettingsButton", "#exportChatStatus", "#importChatStatus"]
    }
  ];

  function categorizeSettings() {
    var list = document.querySelector(".settings-list");
    var saveButton = document.querySelector("#saveSettingsButton");
    if (!list || !saveButton || list.dataset.ctGrouped === "1") return;
    list.dataset.ctGrouped = "1";

    var kids = Array.prototype.slice.call(list.children);
    var claimed = [];

    function nodeFor(sel) {
      for (var i = 0; i < kids.length; i += 1) {
        var k = kids[i];
        if (claimed.indexOf(k) !== -1) continue;
        if ((k.matches && k.matches(sel)) || k.querySelector(sel)) return k;
      }
      return null;
    }

    var menu = el("div", "ct-cat-list");
    var groups = [];

    CATEGORIES.forEach(function (cat) {
      var nodes = [];
      cat.picks.forEach(function (sel) {
        var node = nodeFor(sel);
        if (node) {
          claimed.push(node);
          nodes.push(node);
        }
      });
      if (!nodes.length) return;

      var group = el("div", "ct-group");
      group.hidden = true;

      var bar = el("div", "ct-group-bar");
      var back = el("button", "ct-back");
      back.type = "button";
      back.title = "返回设置分类";
      back.setAttribute("aria-label", "返回设置分类");
      back.appendChild(svg("0 0 24 24", ["M19 12H5", "M11 6l-6 6 6 6"]));
      back.addEventListener("click", function () {
        openMenu();
      });
      bar.appendChild(back);
      bar.appendChild(el("strong", null, cat.title));
      group.appendChild(bar);

      nodes.forEach(function (n) {
        group.appendChild(n);
      });

      var row = el("div", "ct-save-row");
      var save = el("button", "primary-button ct-save", "保存");
      save.type = "button";
      var hint = el("span", "ct-save-hint");
      save.addEventListener("click", function () {
        saveButton.click();
        hint.textContent = "已保存";
        hint.classList.add("is-ok");
        window.setTimeout(function () {
          hint.textContent = "";
          hint.classList.remove("is-ok");
        }, 1800);
      });
      row.appendChild(save);
      row.appendChild(hint);
      if (!group.querySelector("#saveSettingsButton")) {
        group.appendChild(row);
      }

      list.appendChild(group);
      groups.push(group);

      var item = el("button", "ct-cat-item");
      item.type = "button";
      var icon = el("span", "ct-cat-icon");
      icon.appendChild(svg("0 0 24 24", CAT_ICONS[cat.id] || CAT_ICONS.look));
      var copy = el("span", "ct-cat-copy");
      copy.appendChild(el("strong", null, cat.title));
      copy.appendChild(el("small", null, cat.hint));
      var chev = el("span", "ct-cat-chevron");
      chev.appendChild(svg("0 0 24 24", ["M9 6l6 6-6 6"]));
      item.appendChild(icon);
      item.appendChild(copy);
      item.appendChild(chev);
      item.addEventListener("click", function () {
        openGroup(group);
      });
      menu.appendChild(item);
    });

    // 未被分类的残留节点收到「其它」
    var leftovers = kids.filter(function (k) {
      return claimed.indexOf(k) === -1;
    });

    function openMenu() {
      groups.forEach(function (g) {
        g.hidden = true;
      });
      leftovers.forEach(function (n) {
        n.hidden = false;
      });
      menu.hidden = false;
      list.scrollTop = 0;
      if (list.parentNode) list.parentNode.scrollTop = 0;
    }

    function openGroup(group) {
      menu.hidden = true;
      leftovers.forEach(function (n) {
        n.hidden = true;
      });
      groups.forEach(function (g) {
        g.hidden = g !== group;
      });
      if (list.parentNode) list.parentNode.scrollTop = 0;
    }

    list.insertBefore(menu, list.firstChild);
    openMenu();
  }

  /* ---------- 6. 主题色：支持直接输入色号 + 预设 ---------- */

  var PRESETS = ["#17a897", "#3f6fb5", "#8d6fd1", "#c2739a", "#c98b4b", "#4f6b60"];

  function accentControls() {
    var input = document.querySelector("#accentColor");
    if (!input || input.dataset.ctAccent === "1") return;
    input.dataset.ctAccent = "1";

    var row = el("div", "ct-accent-row");
    var hex = el("input", "ct-accent-hex");
    hex.type = "text";
    hex.spellcheck = false;
    hex.setAttribute("aria-label", "主题色色号");
    hex.setAttribute("maxlength", "7");
    hex.placeholder = "#17A897";

    var swatches = el("div", "ct-swatches");
    var buttons = PRESETS.map(function (color) {
      var b = el("button", "ct-swatch");
      b.type = "button";
      b.title = color;
      b.setAttribute("aria-label", "使用 " + color);
      b.style.background = color;
      b.addEventListener("click", function () {
        commit(color);
      });
      swatches.appendChild(b);
      return { color: color, node: b };
    });

    function sync() {
      var value = (input.value || "").toLowerCase();
      hex.value = value.toUpperCase();
      buttons.forEach(function (b) {
        b.node.setAttribute("aria-pressed", b.color === value ? "true" : "false");
      });
    }

    function commit(value) {
      var v = String(value || "").trim();
      if (/^[0-9a-f]{6}$/i.test(v)) v = "#" + v;
      if (!/^#[0-9a-f]{6}$/i.test(v)) {
        sync();
        return;
      }
      input.value = v.toLowerCase();
      input.dispatchEvent(new Event("input", { bubbles: true }));
      input.dispatchEvent(new Event("change", { bubbles: true }));
      sync();
    }

    hex.addEventListener("change", function () {
      commit(hex.value);
    });
    hex.addEventListener("keydown", function (event) {
      if (event.key === "Enter") {
        event.preventDefault();
        commit(hex.value);
      }
    });
    input.addEventListener("input", sync);
    input.addEventListener("change", sync);

    row.appendChild(hex);
    row.appendChild(swatches);
    if (input.parentNode) input.parentNode.appendChild(row);
    sync();
    window.setTimeout(sync, 400);
  }

  /* ---------- 启动 ---------- */

  function init() {
    try { keyboardStateWatch(); } catch (e) {}
    try { watchMessages(); } catch (e) {}
    try { splitMcp(); } catch (e) {}
    try { categorizeSettings(); } catch (e) {}
    try { accentControls(); } catch (e) {}
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
