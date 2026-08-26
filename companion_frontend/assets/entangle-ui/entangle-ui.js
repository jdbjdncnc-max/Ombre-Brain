/* 记忆 / 日程 UI 层：只移动既有 DOM 节点，不请求、不写入、不改业务数据。 */
(function () {
  "use strict";

  var memoryOrder = "desc";

  function element(tag, className, text) {
    var node = document.createElement(tag);
    if (className) node.className = className;
    if (text != null) node.textContent = text;
    return node;
  }

  function memoryDate(value) {
    var timestamp = Date.parse(String(value || ""));
    return Number.isFinite(timestamp) ? new Date(timestamp) : null;
  }

  function memoryDateKey(date) {
    if (!date) return "unknown";
    return [date.getFullYear(), String(date.getMonth() + 1).padStart(2, "0"), String(date.getDate()).padStart(2, "0")].join("-");
  }

  function dayTitle(date) {
    if (!date) return { date: "较早", weekday: "没有创建时间", today: false };
    var today = new Date();
    var isToday = memoryDateKey(today) === memoryDateKey(date);
    return {
      date: (date.getMonth() + 1) + " 月 " + String(date.getDate()).padStart(2, "0") + " 日",
      weekday: ["周日", "周一", "周二", "周三", "周四", "周五", "周六"][date.getDay()],
      today: isToday
    };
  }

  function timelineGroup(key, date) {
    var section = element("section", "em-memory-group");
    section.setAttribute("aria-label", date ? "创建于 " + key : "没有创建时间的记忆");
    var head = element("div", "em-memory-day");
    var label = dayTitle(date);
    head.appendChild(element("span", "em-memory-date", label.date));
    head.appendChild(element("span", "em-memory-weekday", label.weekday));
    if (label.today) head.appendChild(element("span", "em-today-mark", "今天"));
    head.appendChild(element("span", "em-memory-count"));
    var rail = element("div", "em-memory-timeline");
    section.append(head, rail);
    return { key: key, section: section, rail: rail, count: head.querySelector(".em-memory-count"), total: 0 };
  }

  function renderMemoryToolbar(list, groups) {
    var panel = list.parentNode;
    if (!panel) return;
    var old = panel.querySelector(":scope > .em-memory-toolbar");
    if (old) old.remove();
    if (!groups.length) return;

    var toolbar = element("div", "em-memory-toolbar");
    toolbar.setAttribute("aria-label", "记忆时间线导航");
    var seenMonths = Object.create(null);
    groups.forEach(function (group) {
      if (group.key === "unknown") return;
      var month = group.key.slice(0, 7);
      if (seenMonths[month]) return;
      seenMonths[month] = true;
      var button = element("button", "em-month-chip", Number(month.slice(5, 7)) + " 月");
      button.type = "button";
      button.addEventListener("click", function () {
        group.section.scrollIntoView({ behavior: "smooth", block: "start" });
      });
      toolbar.appendChild(button);
    });

    var order = element("button", "em-order-chip", memoryOrder === "desc" ? "↓ 创建时间" : "↑ 创建时间");
    order.type = "button";
    order.title = "切换创建时间排序";
    order.addEventListener("click", function () {
      memoryOrder = memoryOrder === "desc" ? "asc" : "desc";
      unwrapMemoryTimeline(list);
      organizeMemoryTimeline(list);
    });
    toolbar.appendChild(order);
    panel.insertBefore(toolbar, list);
  }

  function unwrapMemoryTimeline(list) {
    var cards = list.querySelectorAll(".em-memory-group .memory-item");
    var more = Array.prototype.slice.call(list.children).filter(function (node) {
      return node.classList && node.classList.contains("load-more-row");
    });
    Array.prototype.slice.call(cards).forEach(function (card) {
      list.appendChild(card);
    });
    more.forEach(function (node) {
      list.appendChild(node);
    });
    Array.prototype.slice.call(list.querySelectorAll(":scope > .em-memory-group")).forEach(function (group) {
      group.remove();
    });
  }

  function organizeMemoryTimeline(list) {
    var directCards = Array.prototype.slice.call(list.children).filter(function (node) {
      return node.classList && node.classList.contains("memory-item");
    });
    if (!directCards.length) {
      if (!list.querySelector(":scope > .em-memory-group")) {
        var panel = list.parentNode;
        var toolbar = panel && panel.querySelector(":scope > .em-memory-toolbar");
        if (toolbar) toolbar.remove();
      }
      return;
    }

    var tail = Array.prototype.slice.call(list.children).filter(function (node) {
      return node.classList && node.classList.contains("load-more-row");
    });
    var sorted = directCards.map(function (card, index) {
      var date = memoryDate(card.dataset.created);
      return { card: card, date: date, index: index };
    }).sort(function (left, right) {
      var leftTime = left.date ? left.date.getTime() : -Infinity;
      var rightTime = right.date ? right.date.getTime() : -Infinity;
      var result = rightTime - leftTime;
      if (memoryOrder === "asc") result = -result;
      return result || left.index - right.index;
    });

    var grouped = Object.create(null);
    var groups = [];
    sorted.forEach(function (entry) {
      var key = memoryDateKey(entry.date);
      if (!grouped[key]) {
        grouped[key] = timelineGroup(key, entry.date);
        groups.push(grouped[key]);
      }
      grouped[key].rail.appendChild(entry.card);
      grouped[key].total += 1;
    });
    groups.forEach(function (group) {
      group.count.textContent = group.total + " 条";
      list.appendChild(group.section);
    });
    tail.forEach(function (node) {
      list.appendChild(node);
    });
    renderMemoryToolbar(list, groups);
  }

  function watchMemoryTimeline() {
    var list = document.querySelector("#memoryList");
    if (!list) return;
    var queued = false;
    function schedule() {
      if (queued) return;
      queued = true;
      window.requestAnimationFrame(function () {
        queued = false;
        organizeMemoryTimeline(list);
      });
    }
    new MutationObserver(schedule).observe(list, { childList: true });
    schedule();
  }

  function sectionFor(selector) {
    var node = document.querySelector(selector);
    return node && node.closest ? node.closest(".schedule-section") : null;
  }

  function setupTodoToggle(todoSection) {
    var form = todoSection.querySelector("#todoForm");
    var titleRow = todoSection.querySelector(".section-title-row");
    if (!form || !titleRow || todoSection.querySelector(".em-todo-toggle")) return;
    var toggle = element("button", "em-todo-toggle", "＋ 记一件事");
    toggle.type = "button";
    toggle.setAttribute("aria-expanded", "false");
    function setOpen(open) {
      form.hidden = !open;
      toggle.setAttribute("aria-expanded", open ? "true" : "false");
      toggle.textContent = open ? "－ 收起待办编辑" : "＋ 记一件事";
    }
    toggle.addEventListener("click", function () {
      setOpen(form.hidden);
    });
    form.addEventListener("submit", function () {
      window.setTimeout(function () {
        if (!todoSection.querySelector("#todoCaptureStatus")?.textContent.includes("缺少")) setOpen(false);
      }, 0);
    });
    titleRow.insertAdjacentElement("afterend", toggle);
    setOpen(false);
  }

  function setupCourseToggle(courseSection) {
    var termForm = courseSection.querySelector("#termForm");
    var textarea = courseSection.querySelector("#courseImportText");
    var buttonRow = textarea && textarea.nextElementSibling;
    var titleRow = courseSection.querySelector(".section-title-row");
    if (!termForm || !textarea || !titleRow || courseSection.querySelector(".em-course-toggle")) return;
    var settings = element("div", "em-course-settings");
    settings.hidden = true;
    settings.append(termForm, textarea);
    if (buttonRow && buttonRow.classList.contains("button-row")) settings.appendChild(buttonRow);
    courseSection.appendChild(settings);
    var toggle = element("button", "em-course-toggle", "课表设置 ›");
    toggle.type = "button";
    toggle.setAttribute("aria-expanded", "false");
    toggle.addEventListener("click", function () {
      var open = settings.hidden;
      settings.hidden = !open;
      toggle.setAttribute("aria-expanded", open ? "true" : "false");
      toggle.textContent = open ? "收起设置⌃" : "课表设置 ›";
    });
    titleRow.appendChild(toggle);
  }

  function arrangeSchedule() {
    var view = document.querySelector("#scheduleView");
    var todo = sectionFor("#todoForm");
    var course = sectionFor("#termForm");
    var week = sectionFor("#weekGrid");
    var today = sectionFor("#scheduleTimeline");
    var upcoming = sectionFor("#upcomingScheduleList");
    if (!view || !todo || !course || !week || !today || !upcoming) return;
    setupTodoToggle(todo);
    setupCourseToggle(course);
    view.insertBefore(today, course);
    view.insertBefore(upcoming, course);
  }

  function init() {
    try { watchMemoryTimeline(); } catch (error) {}
    try { arrangeSchedule(); } catch (error) {}
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
