const lanes = [
  { key: "inbox", label: "Inbox", statuses: ["inbox"] },
  { key: "pending", label: "Pending", statuses: ["pending"] },
  { key: "in_progress", label: "In Progress", statuses: ["in_progress"] },
  { key: "needs_approval", label: "Needs Approval", statuses: ["needs_approval"] },
  { key: "done", label: "Done", statuses: ["done"] },
  { key: "closed", label: "Closed", statuses: ["failed", "canceled"] }
];
const activityConversationTypes = new Set([
  "manager_command",
  "manager_file_change",
  "manager_delegation",
  "manager_item",
  "manager_stream",
  "manager_completed",
  "worker_command",
  "worker_file_change",
  "worker_delegation",
  "worker_item",
  "worker_stream",
  "worker_completed",
  "worker_failed",
  "operator_command",
  "operator_file_change",
  "operator_delegation",
  "operator_item",
  "operator_stream",
  "operator_completed",
  "operator_failed",
  "task_failed"
]);
const board = document.getElementById("board");
const activityPanelEl = document.getElementById("activityPanel");
const activityPanelToggleEl = document.getElementById("activityPanelToggle");
const activityEl = document.getElementById("activity");
const agentPanelEl = document.getElementById("agentPanel");
const agentPanelToggleEl = document.getElementById("agentPanelToggle");
const agentsEl = document.getElementById("agents");
const directoryPanelEl = document.getElementById("directoryPanel");
const directoryPanelToggleEl = document.getElementById("directoryPanelToggle");
const directoryTreeEl = document.getElementById("directoryTree");
const taskFormPanelEl = document.getElementById("taskFormPanel");
const taskFormPanelToggleEl = document.getElementById("taskFormPanelToggle");
const taskFormEl = document.getElementById("taskForm");
const queueTaskFormEl = document.getElementById("queueTaskForm");
const queuePanelEl = document.getElementById("queuePanel");
const queuePanelToggleEl = document.getElementById("queuePanelToggle");
const statusline = document.getElementById("statusline");
const lockline = document.getElementById("lockline");
const safetyRefreshIntervalMs = 500;
const expandedTasks = new Set();
const expandedAgents = new Set();
const collapsedDirectories = new Set();
const knownDirectoryPaths = new Set();
const expandedActivityDetails = new Set();
const laneScrollPositions = new Map();
const tasksById = new Map();
let lastTaskRenderKey = "";
let lastActivityRenderKey = "";
let lastAgentRenderKey = "";
let lastDirectoryRenderKey = "";
let safetyRefreshId = null;
let loadPromise = null;
let loadSequence = 0;
let laneSyncSequence = 0;
let eventSource = null;
let lastLoadedAt = null;
let lastTaskCount = 0;
let refreshQueued = false;
let statuslineError = "";
let statuslineTimerId = null;
let activityPanelCollapsed = true;
let agentPanelCollapsed = true;
let directoryPanelCollapsed = true;
let taskFormPanelCollapsed = true;
let queuePanelCollapsed = true;

class RequestError extends Error {
  constructor(message, status) {
    super(message);
    this.name = "RequestError";
    this.status = status;
  }
}

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function renderInlineMarkdown(value) {
  return escapeHtml(value)
    .replace(/`([^`]+)`/g, "<code>$1</code>")
    .replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>")
    .replace(/\*([^*]+)\*/g, "<em>$1</em>");
}

function renderMarkdown(value) {
  const lines = String(value ?? "").replace(/\r\n/g, "\n").split("\n");
  const html = [];
  let paragraph = [];
  let listType = "";
  let codeLines = [];
  let inCodeBlock = false;

  function closeParagraph() {
    if (!paragraph.length) return;
    html.push(`<p>${paragraph.map(renderInlineMarkdown).join(" ")}</p>`);
    paragraph = [];
  }

  function closeList() {
    if (!listType) return;
    html.push(`</${listType}>`);
    listType = "";
  }

  function openList(type) {
    if (listType === type) return;
    closeParagraph();
    closeList();
    html.push(`<${type}>`);
    listType = type;
  }

  for (const line of lines) {
    const trimmed = line.trim();
    if (trimmed.startsWith("```")) {
      if (inCodeBlock) {
        html.push(`<pre><code>${escapeHtml(codeLines.join("\n"))}</code></pre>`);
        codeLines = [];
        inCodeBlock = false;
      } else {
        closeParagraph();
        closeList();
        inCodeBlock = true;
      }
      continue;
    }
    if (inCodeBlock) {
      codeLines.push(line);
      continue;
    }
    if (!trimmed) {
      closeParagraph();
      closeList();
      continue;
    }

    const heading = /^(#{1,3})\s+(.+)$/.exec(trimmed);
    if (heading) {
      closeParagraph();
      closeList();
      const level = Number(heading[1].length) + 3;
      html.push(`<h${level}>${renderInlineMarkdown(heading[2])}</h${level}>`);
      continue;
    }

    const unordered = /^[-*]\s+(.+)$/.exec(trimmed);
    if (unordered) {
      openList("ul");
      html.push(`<li>${renderInlineMarkdown(unordered[1])}</li>`);
      continue;
    }

    const ordered = /^\d+\.\s+(.+)$/.exec(trimmed);
    if (ordered) {
      openList("ol");
      html.push(`<li>${renderInlineMarkdown(ordered[1])}</li>`);
      continue;
    }

    closeList();
    paragraph.push(trimmed);
  }

  if (inCodeBlock) {
    html.push(`<pre><code>${escapeHtml(codeLines.join("\n"))}</code></pre>`);
  }
  closeParagraph();
  closeList();
  return html.join("");
}

function stableRenderKey(value) {
  return JSON.stringify(value);
}

async function request(path, options = {}) {
  const response = await fetch(path, {
    credentials: "same-origin",
    headers: { "Content-Type": "application/json" },
    ...options
  });
  if (!response.ok) {
    const body = await response.text();
    let message = body;
    try {
      const parsed = JSON.parse(body);
      message = parsed.error || body;
    } catch (_) {
      message = body;
    }
    throw new RequestError(message || `Request failed: ${response.status}`, response.status);
  }
  return response.json();
}

async function load(sequence) {
  const taskData = await request("/api/tasks");
  if (sequence !== loadSequence) return;
  render(taskData.tasks);

  const [activityData, agentData, directoryData, runtimeLockData] = await Promise.allSettled([
    request("/api/activity"),
    request("/api/agents"),
    request("/api/directory"),
    request("/api/runtime-lock")
  ]);
  if (sequence !== loadSequence) return;
  if (activityData.status === "fulfilled") {
    renderActivity(activityData.value.manager_activity);
  } else {
    renderActivity([]);
  }
  if (agentData.status === "fulfilled") {
    renderAgents(agentData.value.agents);
  } else {
    renderAgents([]);
  }
  if (directoryData.status === "fulfilled") {
    renderDirectory(directoryData.value.directory);
  } else {
    lastDirectoryRenderKey = "";
    directoryTreeEl.innerHTML = '<div class="meta">Directory unavailable</div>';
  }
  if (runtimeLockData.status === "fulfilled") {
    renderRuntimeLock(runtimeLockData.value.lock);
  } else {
    renderRuntimeLock(null, "Lock unavailable");
  }
  lastLoadedAt = new Date();
  lastTaskCount = taskData.tasks.length;
  statuslineError = "";
  renderStatusline();
}

function renderStatusline() {
  if (statuslineError) {
    statusline.textContent = statuslineError;
    return;
  }
  if (!lastLoadedAt) {
    statusline.textContent = "Loading...";
    return;
  }
  const seconds = Math.max(0, Math.floor((Date.now() - lastLoadedAt.getTime()) / 1000));
  const age = seconds < 2 ? "just now" : `${seconds}s ago`;
  statusline.textContent = `Updated ${age} (${lastLoadedAt.toLocaleTimeString()}) - ${lastTaskCount} tasks`;
}

function renderRuntimeLock(lock, fallback = "") {
  if (!lock) {
    lockline.textContent = fallback || "Lock idle";
    lockline.className = "lockline idle";
    return;
  }
  const owner = [lock.owner_plane, lock.owner_id].filter(Boolean).join(" ");
  const task = lock.task_id ? ` task #${lock.task_id}` : "";
  const ownerClass = ["task", "operator"].includes(lock.owner_plane) ? lock.owner_plane : "idle";
  lockline.textContent = lock.expired ? `Lock expired: ${owner}${task}` : `Lock: ${owner}${task}`;
  lockline.className = `lockline ${lock.expired ? "expired" : ownerClass}`;
}

function handleLoadError(error) {
  if (error instanceof RequestError && error.status === 401) {
    statuslineError = "Session expired. Redirecting to login...";
    renderStatusline();
    window.location.assign("/login");
    return;
  }
  statuslineError = error.message;
  renderStatusline();
}

function refresh(options = {}) {
  if (loadPromise) {
    if (options.force) {
      refreshQueued = true;
      loadSequence += 1;
    }
    return loadPromise;
  }
  const sequence = ++loadSequence;
  let promise;
  promise = load(sequence)
    .catch(error => {
      if (sequence === loadSequence) {
        handleLoadError(error);
      }
    })
    .finally(() => {
      if (loadPromise === promise) {
        loadPromise = null;
        if (refreshQueued) {
          refreshQueued = false;
          refresh({ force: true });
        }
      }
    });
  loadPromise = promise;
  return loadPromise;
}

function startSafetyPolling() {
  if (safetyRefreshId) return;
  safetyRefreshId = setInterval(refresh, safetyRefreshIntervalMs);
}

function connectEventStream() {
  startSafetyPolling();
  if (!("EventSource" in window)) {
    return;
  }
  eventSource = new EventSource("/api/stream");
  eventSource.addEventListener("snapshot", () => refresh({ force: true }));
  eventSource.addEventListener("update", () => refresh({ force: true }));
  eventSource.onopen = () => {
    startSafetyPolling();
  };
  eventSource.onerror = () => {
    startSafetyPolling();
  };
}

function render(tasks) {
  rememberTasks(tasks);
  const renderKey = stableRenderKey({
    tasks,
    expanded: [...expandedTasks].sort((a, b) => a - b)
  });
  if (renderKey === lastTaskRenderKey) return;
  lastTaskRenderKey = renderKey;

  const pageScroll = { x: window.scrollX, y: window.scrollY };
  captureLaneScroll();
  const grouped = Object.fromEntries(lanes.map(lane => [lane.key, []]));
  for (const task of tasks) {
    const lane = lanes.find(candidate => candidate.statuses.includes(task.status));
    (grouped[lane?.key || "closed"] ||= []).push(task);
  }
  board.innerHTML = lanes.map(lane => `
    <div class="lane" data-status="${escapeHtml(lane.key)}">
      <h3>${escapeHtml(lane.label)} (${grouped[lane.key].length})</h3>
      ${grouped[lane.key].map(taskCard).join("") || '<div class="meta">No tasks</div>'}
    </div>
  `).join("");
  if (!queuePanelCollapsed) {
    syncLaneHeight(pageScroll, ++laneSyncSequence);
  }
}

function rememberTasks(tasks) {
  tasksById.clear();
  tasks.forEach(task => {
    tasksById.set(Number(task.id), task);
  });
}

function taskLabel(task) {
  const title = String(task.title || "").trim();
  return title ? `#${task.id} ${title}` : `#${task.id}`;
}

function captureLaneScroll() {
  document.querySelectorAll(".lane").forEach(lane => {
    laneScrollPositions.set(lane.dataset.status, lane.scrollTop);
  });
}

function syncLaneHeight(pageScroll, syncSequence) {
  requestAnimationFrame(() => {
    if (syncSequence !== laneSyncSequence) return;
    const lanes = [...document.querySelectorAll(".lane")];
    const rows = [];

    lanes.forEach(lane => {
      lane.style.removeProperty("--lane-height");
    });

    lanes.forEach(lane => {
      const top = lane.offsetTop;
      let row = rows.find(candidate => Math.abs(candidate.top - top) < 4);
      if (!row) {
        row = { top, lanes: [] };
        rows.push(row);
      }
      row.lanes.push(lane);
    });

    rows.forEach(row => {
      const expandedCards = row.lanes
        .flatMap(lane => [...lane.querySelectorAll(".card.expanded")]);
      let targetHeight = 220;

      if (expandedCards.length) {
        const largestCard = Math.max(...expandedCards.map(card => card.getBoundingClientRect().height));
        const sampleLane = row.lanes[0];
        const sampleHeading = sampleLane?.querySelector("h3");
        const laneStyle = sampleLane ? getComputedStyle(sampleLane) : null;
        const headingStyle = sampleHeading ? getComputedStyle(sampleHeading) : null;
        const laneChrome =
          (sampleHeading?.getBoundingClientRect().height || 0) +
          (laneStyle ? parseFloat(laneStyle.paddingTop) + parseFloat(laneStyle.paddingBottom) : 20) +
          (laneStyle ? parseFloat(laneStyle.borderTopWidth) + parseFloat(laneStyle.borderBottomWidth) : 2) +
          (headingStyle ? parseFloat(headingStyle.marginBottom) : 10) +
          10;
        targetHeight = Math.ceil(largestCard + laneChrome);
      }

      row.lanes.forEach(lane => {
        lane.style.setProperty("--lane-height", `${targetHeight}px`);
      });
    });

    lanes.forEach(lane => {
      lane.scrollTop = laneScrollPositions.get(lane.dataset.status) || 0;
    });
    window.scrollTo(pageScroll.x, pageScroll.y);
  });
}

function taskCard(task) {
  const expanded = expandedTasks.has(task.id);
  const events = task.events.map(event => `
    <div class="event">${escapeHtml(event.created_at)} - ${escapeHtml(event.type)}: ${escapeHtml(event.message)}</div>
  `).join("");
  const controls = controlsFor(task);
  return `
    <article class="card ${escapeHtml(task.status)} ${expanded ? "expanded" : ""}">
      <button class="card-toggle" type="button" onclick="toggleTask(${task.id})" aria-expanded="${expanded ? "true" : "false"}">
        <div class="title">
          <span class="title-text">${escapeHtml(taskLabel(task))}</span>
        </div>
      </button>
      ${expanded ? `
        <div class="card-body">
          <div class="meta">status ${escapeHtml(task.status)} - priority ${task.priority} - attempts ${task.attempt_count}</div>
          ${task.description ? `<pre>${escapeHtml(task.description)}</pre>` : ""}
          ${task.result ? `<div class="markdown">${renderMarkdown(task.result)}</div>` : ""}
          ${task.error ? `<pre>${escapeHtml(task.error)}</pre>` : ""}
          ${controls}
          <div class="events">${events}</div>
        </div>
      ` : ""}
    </article>
  `;
}

function renderActivity(managerActivity) {
  const renderKey = stableRenderKey({
    managerActivity: managerActivity || [],
    expandedActivityDetails: [...expandedActivityDetails].sort()
  });
  if (renderKey === lastActivityRenderKey) return;
  lastActivityRenderKey = renderKey;

  const managers = Array.isArray(managerActivity) ? managerActivity : [];
  activityEl.innerHTML = managers.map(managerActivityCard).join("") || '<div class="meta">No managers</div>';
  bindActivityDetailToggles();
  requestAnimationFrame(syncActivityDetailOverflow);
}

function managerActivityCard(manager) {
  const activityStatus = manager.activity_status || "empty";
  const task = manager.task;
  const taskText = task ? taskLabel(task) : "No activity yet";
  const events = (manager.events || []).filter(isManagerConversationEvent);
  const entries = managerActivityEntries(events);
  const messages = task
    ? [
        taskPromptMessage(task),
        entries.length ? entries.map(activityEntry).join("") : '<div class="meta">Waiting for activity</div>'
      ].join("")
    : '<div class="meta">No activity yet</div>';
  return `
    <article class="manager-activity ${escapeHtml(activityStatus)}">
      <div class="manager-activity-head">
        <div>
          <div class="manager-activity-name">${escapeHtml(manager.name)}</div>
          <div class="manager-activity-role">${escapeHtml(manager.role_key)}</div>
        </div>
        <span class="agent-state ${escapeHtml(activityStatus)}">${escapeHtml(managerActivityStatusLabel(activityStatus))}</span>
      </div>
      <div class="activity-task">${escapeHtml(taskText)}</div>
      <div class="chat-log">${messages}</div>
    </article>
  `;
}

function managerActivityStatusLabel(status) {
  if (status === "current") return "current task";
  if (status === "latest") return "latest task";
  return "empty";
}

function isManagerConversationEvent(event) {
  return activityConversationTypes.has(event.type);
}

function managerActivityEntries(events) {
  const entries = [];
  const detailIndexes = new Map();
  events.forEach(event => {
    const detail = parseActivityDetail(event);
    if (!detail) {
      entries.push({ kind: "message", event });
      return;
    }
    const entry = { kind: "detail", detail };
    if (detailIndexes.has(detail.key)) {
      entries[detailIndexes.get(detail.key)] = entry;
      return;
    }
    detailIndexes.set(detail.key, entries.length);
    entries.push(entry);
  });
  return entries;
}

function activityEntry(entry) {
  if (entry.kind === "detail") return activityDetail(entry.detail);
  return activityMessage(entry.event);
}

function parseActivityDetail(event) {
  const actor = activityActor(event.type) || "system";
  if (isActorEventType(event.type, "command")) {
    try {
      const payload = JSON.parse(event.message || "{}");
      const command = commandLineText(payload.command || "");
      if (!command) return null;
      const state = payload.state === "ran" ? "ran" : "running";
      const key = String(payload.command_id || command);
      return {
        key: `${actor}:command:${key}`,
        actor,
        state,
        content: command,
        oneLine: command
      };
    } catch (_error) {
      return null;
    }
  }
  if (isActorEventType(event.type, "file_change")) {
    try {
      const payload = JSON.parse(event.message || "{}");
      const details = fileChangeText(payload.changes || []);
      const state = payload.state === "ran" ? "ran" : "running";
      const key = String(payload.change_id || details || event.id);
      return {
        key: `${actor}:file:${key}`,
        actor,
        state,
        content: details || "file change",
        oneLine: commandLineText(details || "file change")
      };
    } catch (_error) {
      return null;
    }
  }
  if (isActorEventType(event.type, "delegation")) {
    try {
      const payload = JSON.parse(event.message || "{}");
      const detail = commandLineText(payload.detail || "worker delegation");
      const content = String(payload.content || detail).trim() || detail;
      const state = payload.state === "ran" ? "ran" : "running";
      const key = String(payload.delegation_id || detail || event.id);
      return {
        key: `${actor}:delegation:${key}`,
        actor,
        state,
        content,
        oneLine: detail
      };
    } catch (_error) {
      return null;
    }
  }
  if (!isActorEventType(event.type, "item")) return null;
  const text = String(event.message || "");
  const delegationMatch = text.match(/^item\.(started|completed): collab_tool_call(?:\s+\S+)?(?:\s+(item_[^\s]+))?$/);
  if (delegationMatch) {
    return {
      key: `${actor}:legacy-delegation:${delegationMatch[2] || text}`,
      actor,
      state: delegationMatch[1] === "completed" ? "ran" : "running",
      content: "worker delegation",
      oneLine: "worker delegation"
    };
  }
  const commandMatch = text.match(/^item\.(started|completed): command[^\n]*\n([\s\S]+)$/);
  if (commandMatch) {
    const command = commandLineText(commandMatch[2]);
    if (!command) return null;
    return {
      key: `${actor}:legacy-command:${command}`,
      actor,
      state: commandMatch[1] === "completed" ? "ran" : "running",
      content: command,
      oneLine: command
    };
  }
  const webSearchMatch = text.match(/^item\.(started|completed): web_search(?:[^\n]*)?$/);
  if (webSearchMatch) {
    const searchIdMatch = text.match(/\bws_[A-Za-z0-9_-]+\b/);
    const detail = searchIdMatch ? `web search ${searchIdMatch[0]}` : "web search";
    return {
      key: `${actor}:legacy-web-search:${searchIdMatch ? searchIdMatch[0] : detail}`,
      actor,
      state: webSearchMatch[1] === "completed" ? "ran" : "running",
      content: detail,
      oneLine: detail
    };
  }
  const fileMatch = text.match(/^item\.(started|completed): file_change[^\n]*(?:\n([\s\S]+))?$/);
  if (!fileMatch) return null;
  const details = String(fileMatch[2] || "").trim() || "file change";
  return {
    key: `${actor}:legacy-file:${details}`,
    actor,
    state: fileMatch[1] === "completed" ? "ran" : "running",
    content: details,
    oneLine: commandLineText(details)
  };
}

function activityDetail(detail) {
  const expanded = expandedActivityDetails.has(detail.key);
  const actor = detail.actor || "system";
  const collapsedText = commandLineText(detail.oneLine || detail.content);
  const textHtml = expanded ? formatChatText(detail.content) : escapeHtml(collapsedText);
  const fullTextAttribute = expanded ? "" : ` data-full-text="${escapeHtml(collapsedText)}"`;
  const ellipsis = !expanded && collapsedText
    ? '<span class="activity-ellipsis">...</span>'
    : "";
  return `
    <button class="activity-detail-line ${escapeHtml(actor)} ${expanded ? "expanded" : "collapsed"}" type="button" data-activity-detail-key="${escapeHtml(detail.key)}" aria-expanded="${expanded ? "true" : "false"}">
      <span class="activity-state ${escapeHtml(detail.state)}">${escapeHtml(detail.state)}</span><span class="activity-colon">:</span>
      <span class="activity-detail-text"${fullTextAttribute}>${textHtml}</span>${ellipsis}
    </button>
  `;
}

function bindActivityDetailToggles() {
  activityEl.querySelectorAll("[data-activity-detail-key]").forEach(button => {
    button.addEventListener("click", () => toggleActivityDetail(button.dataset.activityDetailKey));
  });
}

function syncActivityDetailOverflow() {
  activityEl.querySelectorAll(".activity-detail-line.collapsed").forEach(button => {
    const textEl = button.querySelector(".activity-detail-text");
    if (!textEl) return;
    const fullText = textEl.dataset.fullText || "";
    textEl.textContent = fullText;
    button.classList.remove("is-truncated");
    if (textEl.clientWidth <= 0) return;
    if (!fullText || textEl.scrollWidth <= textEl.clientWidth + 1) return;

    button.classList.add("is-truncated");
    const chars = [...fullText];
    let low = 0;
    let high = chars.length;
    while (low < high) {
      const middle = Math.ceil((low + high) / 2);
      textEl.textContent = chars.slice(0, middle).join("").trimEnd();
      if (textEl.scrollWidth <= textEl.clientWidth + 1) {
        low = middle;
      } else {
        high = middle - 1;
      }
    }
    textEl.textContent = chars.slice(0, low).join("").trimEnd();
  });
}

function toggleActivityDetail(key) {
  if (expandedActivityDetails.has(key)) {
    expandedActivityDetails.delete(key);
  } else {
    expandedActivityDetails.add(key);
  }
  refresh({ force: true });
}

function fileChangeText(changes) {
  if (!Array.isArray(changes)) return "";
  return changes.map(change => {
    if (!change || typeof change !== "object") return "";
    const kind = String(change.kind || "").trim();
    const path = String(change.path || "").trim();
    return [kind, path].filter(Boolean).join(": ");
  }).filter(Boolean).join("\n");
}

function taskPromptMessage(task) {
  const title = String(task.title || "").trim();
  const description = String(task.description || "").trim();
  const acceptance = String(task.acceptance_criteria || "").trim();
  const content = [
    title ? `Title: ${title}` : "",
    description,
    acceptance ? `Acceptance: ${acceptance}` : ""
  ].filter(Boolean).join("\n\n") || taskLabel(task);
  return `
    <div class="chat-message user">
      <div class="chat-label">task request</div>
      <div class="chat-content">${formatChatText(content)}</div>
    </div>
  `;
}

function activityMessage(event) {
  const role = activityRole(event.type);
  return `
    <div class="chat-message ${escapeHtml(role)}">
      <div class="chat-label">${escapeHtml(activityLabel(event.type))} · ${escapeHtml(event.created_at)}</div>
      <div class="chat-content">${formatChatText(event.message)}</div>
    </div>
  `;
}

function activityRole(type) {
  return activityActor(type) || "system";
}

function activityLabel(type) {
  const actor = activityActor(type);
  if (actor && isActorEventType(type, "command")) return `${actor} command`;
  if (actor && isActorEventType(type, "file_change")) return `${actor} file change`;
  if (actor && isActorEventType(type, "delegation")) return `${actor} delegation`;
  if (actor && isActorEventType(type, "item")) return `${actor} action`;
  if (actor && isActorEventType(type, "stream")) return actor;
  if (actor && isActorEventType(type, "completed")) return `${actor} result`;
  if (actor && isActorEventType(type, "failed")) return `${actor} error`;
  if (type === "task_failed") return "manager error";
  return type;
}

function activityActor(type) {
  const value = String(type);
  if (value.startsWith("manager_")) return "manager";
  if (value.startsWith("worker_")) return "worker";
  if (value.startsWith("operator_")) return "operator";
  if (value === "task_failed") return "manager";
  return "";
}

function isActorEventType(type, suffix) {
  const actor = activityActor(type);
  return Boolean(actor) && type === `${actor}_${suffix}`;
}

function formatChatText(value) {
  return escapeHtml(value).replace(/\n/g, "<br>");
}

function commandLineText(value) {
  return String(value || "").replace(/\s+/g, " ").trim();
}

function renderAgents(agents) {
  const renderKey = stableRenderKey({
    agents,
    expanded: [...expandedAgents].sort()
  });
  if (renderKey === lastAgentRenderKey) return;
  lastAgentRenderKey = renderKey;

  agentsEl.innerHTML = agents.map(agentCard).join("") || '<div class="meta">No agents</div>';
}

function agentCard(agent) {
  const expanded = expandedAgents.has(agent.role_key);
  const actor = agentActor(agent.role_key);
  const runtimeStatus = agent.runtime_status || "unknown";
  const keyText = agent.has_key ? "key set" : "key missing";
  const taskText = agent.current_task_id ? `task #${agent.current_task_id}` : "no task";
  const taskRow = actor === "operator" ? "" : `<div>${escapeHtml(taskText)}</div>`;
  const progressRow = agent.progress ? `<div>${escapeHtml(agent.progress)}</div>` : "";
  const heartbeat = agent.heartbeat_at ? agent.heartbeat_at : "no heartbeat";
  const updated = agent.updated_at ? agent.updated_at : "not updated";
  return `
    <article class="agent ${escapeHtml(actor)} ${escapeHtml(runtimeStatus)} ${expanded ? "expanded" : ""}">
      <button class="agent-toggle" type="button" onclick="toggleAgent('${escapeHtml(agent.role_key)}')" aria-expanded="${expanded ? "true" : "false"}">
        <div class="agent-head">
          <div class="agent-name">${escapeHtml(agent.name)}</div>
          <span class="agent-state ${escapeHtml(runtimeStatus)}">${escapeHtml(runtimeStatus)}</span>
        </div>
      </button>
      ${expanded ? `
      <div class="agent-meta meta">
        <div class="agent-name">
          <div class="agent-role">${escapeHtml(agent.role_key)}</div>
        </div>
        <div>handle ${escapeHtml(agent.key_status)} - ${escapeHtml(keyText)}</div>
        ${taskRow}
        ${progressRow}
        <div>${escapeHtml(heartbeat)}</div>
        <div>${escapeHtml(updated)}</div>
      </div>
      ` : ""}
    </article>
  `;
}

function agentActor(roleKey) {
  const value = String(roleKey || "");
  if (value.startsWith("manager.")) return "manager";
  if (value.startsWith("worker.")) return "worker";
  if (value === "operator" || value.startsWith("operator.")) return "operator";
  return "system";
}

function renderDirectory(directory) {
  const items = directory?.items || [];
  rememberDirectoryDefaults(items);
  const renderKey = stableRenderKey({
    directory,
    collapsed: [...collapsedDirectories].sort(),
    panelCollapsed: directoryPanelCollapsed
  });
  if (renderKey === lastDirectoryRenderKey) return;
  lastDirectoryRenderKey = renderKey;

  syncDirectoryPanel();
  if (directoryPanelCollapsed) {
    directoryTreeEl.innerHTML = "";
    return;
  }
  const rows = items.filter(item => !isDirectoryHidden(item)).map(item => {
    const depth = Number(item.depth) || 0;
    const isDir = item.type === "dir";
    const collapsed = collapsedDirectories.has(item.path);
    if (isDir) {
      return `
        <button class="tree-row tree-toggle dir" type="button" data-tree-path="${escapeHtml(item.path)}" style="--depth: ${depth}" aria-expanded="${collapsed ? "false" : "true"}">
          <span class="tree-name">${escapeHtml(item.name)}/</span>
        </button>
      `;
    }
    return `
      <div class="tree-row ${isDir ? "dir" : "file"}" style="--depth: ${depth}">
        <span class="tree-name">${escapeHtml(item.name)}</span>
      </div>
    `;
  }).join("");
  directoryTreeEl.innerHTML = `
    <div class="tree-root">${escapeHtml(directory?.root || ".")}</div>
    ${rows || '<div class="meta">No entries</div>'}
    ${directory?.truncated ? '<div class="meta">More entries hidden</div>' : ""}
  `;
  bindDirectoryToggles();
}

function rememberDirectoryDefaults(items) {
  items.forEach(item => {
    if (item.type !== "dir" || knownDirectoryPaths.has(item.path)) return;
    knownDirectoryPaths.add(item.path);
    collapsedDirectories.add(item.path);
  });
}

function syncActivityPanel() {
  activityPanelEl.classList.toggle("collapsed", activityPanelCollapsed);
  activityEl.hidden = activityPanelCollapsed;
  activityPanelToggleEl.setAttribute("aria-expanded", String(!activityPanelCollapsed));
}

function syncAgentPanel() {
  agentPanelEl.classList.toggle("collapsed", agentPanelCollapsed);
  agentsEl.hidden = agentPanelCollapsed;
  agentPanelToggleEl.setAttribute("aria-expanded", String(!agentPanelCollapsed));
}

function syncDirectoryPanel() {
  directoryPanelEl.classList.toggle("collapsed", directoryPanelCollapsed);
  directoryTreeEl.hidden = directoryPanelCollapsed;
  directoryPanelToggleEl.setAttribute("aria-expanded", String(!directoryPanelCollapsed));
}

function syncTaskFormPanel() {
  taskFormPanelEl.classList.toggle("collapsed", taskFormPanelCollapsed);
  taskFormEl.hidden = taskFormPanelCollapsed;
  taskFormPanelToggleEl.setAttribute("aria-expanded", String(!taskFormPanelCollapsed));
}

function syncQueuePanel() {
  queuePanelEl.classList.toggle("collapsed", queuePanelCollapsed);
  board.hidden = queuePanelCollapsed;
  queuePanelToggleEl.setAttribute("aria-expanded", String(!queuePanelCollapsed));
}

function isDirectoryHidden(item) {
  const parts = String(item.path || "").split("/");
  for (let index = 1; index < parts.length; index += 1) {
    const ancestor = parts.slice(0, index).join("/");
    if (collapsedDirectories.has(ancestor)) return true;
  }
  return false;
}

function bindDirectoryToggles() {
  directoryTreeEl.querySelectorAll("[data-tree-path]").forEach(button => {
    button.addEventListener("click", () => toggleDirectory(button.dataset.treePath));
  });
}

function toggleTask(id) {
  if (expandedTasks.has(id)) {
    expandedTasks.delete(id);
  } else {
    expandedTasks.add(id);
  }
  refresh({ force: true });
}

function toggleAgent(roleKey) {
  if (expandedAgents.has(roleKey)) {
    expandedAgents.delete(roleKey);
  } else {
    expandedAgents.add(roleKey);
  }
  refresh({ force: true });
}

function toggleDirectory(path) {
  if (collapsedDirectories.has(path)) {
    collapsedDirectories.delete(path);
  } else {
    collapsedDirectories.add(path);
  }
  refresh({ force: true });
}

function toggleActivityPanel() {
  activityPanelCollapsed = !activityPanelCollapsed;
  syncActivityPanel();
  if (!activityPanelCollapsed) {
    requestAnimationFrame(syncActivityDetailOverflow);
  }
}

function toggleAgentPanel() {
  agentPanelCollapsed = !agentPanelCollapsed;
  syncAgentPanel();
}

function toggleDirectoryPanel() {
  directoryPanelCollapsed = !directoryPanelCollapsed;
  refresh({ force: true });
}

function toggleTaskFormPanel() {
  taskFormPanelCollapsed = !taskFormPanelCollapsed;
  syncTaskFormPanel();
}

function toggleQueuePanel() {
  queuePanelCollapsed = !queuePanelCollapsed;
  syncQueuePanel();
  if (!queuePanelCollapsed) {
    requestAnimationFrame(() => syncLaneHeight(window.scrollY, ++laneSyncSequence));
  }
}

function controlsFor(task) {
  const buttons = [];
  if (task.status === "inbox") {
    buttons.push(`<button onclick="act(${task.id}, 'queue')">Queue</button>`);
    buttons.push(`<button class="secondary" onclick="moveInboxTaskToForm(${task.id})">Move to form</button>`);
    buttons.push(`<button class="danger" onclick="act(${task.id}, 'delete')">Delete</button>`);
    return `<div class="actions">${buttons.join("")}</div>`;
  }
  if (task.status === "needs_approval") {
    return approvalControls(task);
  }
  if (["failed", "canceled", "done"].includes(task.status)) {
    buttons.push(`<button class="secondary" onclick="act(${task.id}, 'retry')">Retry</button>`);
  }
  if (!["done", "canceled"].includes(task.status)) {
    buttons.push(`<button class="danger" onclick="act(${task.id}, 'cancel')">Cancel</button>`);
  }
  return buttons.length ? `<div class="actions">${buttons.join("")}</div>` : "";
}

function approvalControls(task) {
  const noteId = `approval-note-${task.id}`;
  return `
    <div class="approval-form">
      <label for="${escapeHtml(noteId)}">Note for manager</label>
      <textarea id="${escapeHtml(noteId)}" rows="3" placeholder="Optional note for the next run"></textarea>
      <div class="actions">
        <button onclick="approveWithNote(${task.id})">Approve</button>
        <button class="danger" onclick="act(${task.id}, 'cancel')">Cancel</button>
      </div>
    </div>
  `;
}

function setTaskFormField(name, value) {
  const field = taskFormEl.elements.namedItem(name);
  if (!field) return;
  field.value = String(value ?? "");
}

function editableTaskValues(task) {
  return {
    title: task.title || "",
    description: task.description || "",
    acceptance_criteria: task.acceptance_criteria || "",
    priority: task.priority || 3
  };
}

function fillTaskForm(values) {
  setTaskFormField("title", values.title);
  setTaskFormField("description", values.description);
  setTaskFormField("acceptance_criteria", values.acceptance_criteria);
  setTaskFormField("priority", values.priority);
}

async function moveInboxTaskToForm(id) {
  const task = tasksById.get(Number(id));
  if (!task || task.status !== "inbox") return;

  const values = editableTaskValues(task);
  try {
    await request(`/api/tasks/${id}/delete`, { method: "POST" });
  } catch (error) {
    handleLoadError(error);
    return;
  }

  taskFormEl.reset();
  fillTaskForm(values);
  if (taskFormPanelCollapsed) {
    taskFormPanelCollapsed = false;
    syncTaskFormPanel();
  }
  await refresh({ force: true });
  document.getElementById("title").focus();
}

async function act(id, action) {
  try {
    await request(`/api/tasks/${id}/${action}`, { method: "POST" });
    await refresh({ force: true });
  } catch (error) {
    handleLoadError(error);
  }
}

async function approveWithNote(id) {
  const noteField = document.getElementById(`approval-note-${id}`);
  const note = noteField ? noteField.value : "";
  try {
    await request(`/api/tasks/${id}/approve`, {
      method: "POST",
      body: JSON.stringify({ note })
    });
    await refresh({ force: true });
  } catch (error) {
    handleLoadError(error);
  }
}

async function createTaskFromForm(status) {
  const form = new FormData(taskFormEl);
  const payload = Object.fromEntries(form.entries());
  if (status) payload.status = status;
  await request("/api/tasks", {
    method: "POST",
    body: JSON.stringify(payload)
  });
  taskFormEl.reset();
  await refresh({ force: true });
}

taskFormEl.addEventListener("submit", async event => {
  event.preventDefault();
  try {
    await createTaskFromForm("inbox");
  } catch (error) {
    handleLoadError(error);
  }
});

queueTaskFormEl.addEventListener("click", async () => {
  try {
    await createTaskFromForm("pending");
  } catch (error) {
    handleLoadError(error);
  }
});

document.getElementById("clearTaskForm").addEventListener("click", () => {
  taskFormEl.reset();
  document.getElementById("title").focus();
});

activityPanelToggleEl.addEventListener("click", toggleActivityPanel);
agentPanelToggleEl.addEventListener("click", toggleAgentPanel);
directoryPanelToggleEl.addEventListener("click", toggleDirectoryPanel);
taskFormPanelToggleEl.addEventListener("click", toggleTaskFormPanel);
queuePanelToggleEl.addEventListener("click", toggleQueuePanel);
document.getElementById("refresh").addEventListener("click", () => refresh({ force: true }));
window.addEventListener("resize", syncActivityDetailOverflow);
syncActivityPanel();
syncAgentPanel();
syncDirectoryPanel();
syncTaskFormPanel();
syncQueuePanel();
refresh();
connectEventStream();
statuslineTimerId = setInterval(renderStatusline, 1000);
