// In-memory stand-in for the plugin's REST backend, used when the AstrBot bridge is absent (standalone demo).
// `request(method, endpoint, payload)` implements the contract in docs/design.md section 7. Everything crosses a
// JSON boundary, like the real wire. Demo-only switch: `?mockFail=<substring of "METHOD endpoint">` makes
// matching calls reject, e.g. `?mockFail=POST spaces/1/tasks/3` (used to exercise error display and rollback).
import { EVENTS, SPACES, TASKS, TIMEZONE, WEBUI_USER } from "./mock-data.js";
import { LIMITS, PRIORITY_LABEL, STATUS_LABEL, formatDateTime, isOverdue, nowSeconds, userKey } from "./format.js";

const LATENCY_MS = 70;
const INVITE_TTL_SECONDS = 7 * 86400;
const INVITE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789";
const ADMIN_LABEL = "admin（WebUI）";
const MOCK_FAIL = new URLSearchParams(location.search).get("mockFail");

const spaces = clone(SPACES);
const tasks = clone(TASKS);
const events = clone(EVENTS);

function fail(status, message) {
  throw Object.assign(new Error(message), { status });
}

// JSON round trip like the real wire (also strips Vue reactive proxies, which structuredClone rejects).
function clone(value) {
  return JSON.parse(JSON.stringify(value ?? null));
}

function randomString(alphabet, length) {
  const bytes = crypto.getRandomValues(new Uint8Array(length));
  return Array.from(bytes, (byte) => alphabet[byte % alphabet.length]).join("");
}

function getSpace(id) {
  const space = spaces.find((s) => s.id === Number(id));
  if (!space) fail(404, "空间不存在");
  return space;
}

function getTask(spaceId, seq) {
  const found = tasks.find((t) => t.space_id === Number(spaceId) && t.seq === Number(seq));
  if (!found) fail(404, "任务不存在");
  return found;
}

function summarize(space) {
  const own = tasks.filter((t) => t.space_id === space.id);
  const now = nowSeconds();
  const counts = { todo: 0, doing: 0, done: 0, archived: 0 };
  own.forEach((t) => counts[t.status]++);
  return {
    id: space.id,
    name: space.name,
    owner: space.owner,
    created_at: space.created_at,
    binding_count: space.bindings.length,
    member_count: space.members.length,
    task_counts: counts,
    overdue_count: own.filter((t) => isOverdue(t, now)).length,
  };
}

function detail(space) {
  return {
    ...summarize(space),
    bindings: space.bindings,
    members: space.members,
    invite: space.invite && space.invite.expires_at > nowSeconds() ? { expires_at: space.invite.expires_at } : null,
  };
}

/** Appends an operation-log entry; `summary` is the already-rendered sentence (the real server renders it). */
function log(spaceId, taskSeq, action, summary) {
  events.push({ space_id: spaceId, kind: taskSeq == null ? "space" : "task", task_seq: taskSeq, action, summary, actor_name: ADMIN_LABEL, created_at: nowSeconds() });
}

const publicEvent = ({ space_id, ...rest }) => rest;
const publicTask = (task) => ({ ...task, overdue: isOverdue(task, nowSeconds()) });

// ---- validation (the real backend enforces the same limits for every entry point) ----

function cleanTitle(value) {
  const title = String(value ?? "").trim();
  if (!title) fail(400, "标题不能为空");
  if ([...title].length > LIMITS.title) fail(400, `标题最多 ${LIMITS.title} 字`);
  return title;
}

function cleanTags(value) {
  const tags = [...new Set((value ?? []).map((t) => String(t).trim()).filter(Boolean))];
  if (tags.length > LIMITS.tags) fail(400, `标签最多 ${LIMITS.tags} 个`);
  if (tags.some((t) => [...t].length > LIMITS.tag)) fail(400, `单个标签最多 ${LIMITS.tag} 字`);
  return tags;
}

function cleanAssignees(space, value) {
  const refs = value ?? [];
  if (refs.length > LIMITS.assignees) fail(400, `负责人最多 ${LIMITS.assignees} 人`);
  return refs.map((ref) => {
    const found = space.members.find((m) => userKey(m) === userKey(ref));
    if (!found) fail(400, "只能指派给空间成员");
    return { platform_id: found.platform_id, user_id: found.user_id, nickname: found.nickname };
  });
}

function cleanDescription(value) {
  const text = String(value ?? "");
  if ([...text].length > LIMITS.description) fail(400, `描述最多 ${LIMITS.description} 字`);
  return text;
}

function cleanPriority(value) {
  if (value == null || value === "") return null;
  if (!["high", "medium", "low"].includes(value)) fail(400, "优先级无效");
  return value;
}

// ---- task mutations ----

const sameJson = (a, b) => JSON.stringify(a) === JSON.stringify(b);

function applyPatch(space, task, patch) {
  const changes = {};
  const set = (field, value) => {
    if (!sameJson(task[field], value)) {
      changes[field] = [task[field], value];
      task[field] = value;
    }
  };
  if ("title" in patch) set("title", cleanTitle(patch.title));
  if ("description" in patch) set("description", cleanDescription(patch.description));
  if ("priority" in patch) set("priority", cleanPriority(patch.priority));
  if ("tags" in patch) set("tags", cleanTags(patch.tags));
  if ("due_at" in patch) {
    set("due_at", patch.due_at ?? null);
  }
  const before = task.assignees.map((a) => a.nickname);
  if ("assignees" in patch) {
    const next = cleanAssignees(space, patch.assignees);
    if (!sameJson(task.assignees.map(userKey), next.map(userKey))) {
      task.assignees = next;
      const after = next.map((a) => a.nickname);
      changes.assignees = { added: after.filter((n) => !before.includes(n)), removed: before.filter((n) => !after.includes(n)) };
    }
  }
  if ("status" in patch && patch.status !== task.status) {
    if (!["todo", "doing", "done", "archived"].includes(patch.status)) fail(400, "状态无效");
    changes.status = [task.status, patch.status];
    task.status = patch.status;
    if (patch.status === "done") task.done_at = nowSeconds();
    else if (patch.status !== "archived") task.done_at = null;
  }
  return changes;
}

const dueText = (value) => (value == null ? "无" : formatDateTime(value));
const FIELD_SUMMARY = {
  title: ([, next]) => `标题改为「${next}」`,
  description: () => "修改了描述",
  due_at: ([old, next]) => `截止时间 ${dueText(old)} → ${dueText(next)}`,
  priority: ([old, next]) => `优先级 ${old ? PRIORITY_LABEL[old] : "无"} → ${next ? PRIORITY_LABEL[next] : "无"}`,
  tags: ([old, next]) => `标签 ${old.join("、") || "无"} → ${next.join("、") || "无"}`,
};

function recordChanges(task, changes) {
  const { status, assignees, ...fields } = changes;
  const write = (action, summary) => log(task.space_id, task.seq, action, summary);
  if (status) {
    write(status[1] === "archived" ? "archive" : "status", status[1] === "archived" ? "归档了任务" : `将状态由 ${STATUS_LABEL[status[0]]} 改为 ${STATUS_LABEL[status[1]]}`);
  }
  if (assignees) {
    const parts = [];
    if (assignees.added.length) parts.push(`指派给 ${assignees.added.join("、")}`);
    if (assignees.removed.length) parts.push(`取消了 ${assignees.removed.join("、")} 的指派`);
    write("assign", parts.join("，"));
  }
  const text = Object.entries(fields).map(([field, change]) => FIELD_SUMMARY[field](change)).join("；");
  if (text) write("update", text);
}

function createTask(space, body) {
  const task = {
    space_id: space.id,
    seq: Math.max(0, ...tasks.filter((t) => t.space_id === space.id).map((t) => t.seq), ...events.filter((e) => e.space_id === space.id).map((e) => e.task_seq ?? 0)) + 1,
    title: cleanTitle(body.title),
    status: "todo",
    assignees: cleanAssignees(space, body.assignees),
    due_at: body.due_at ?? null,
    tags: cleanTags(body.tags),
    priority: cleanPriority(body.priority),
    description: cleanDescription(body.description),
    creator: { ...WEBUI_USER },
    created_at: nowSeconds(),
    updated_at: nowSeconds(),
    done_at: null,
  };
  tasks.push(task);
  log(space.id, task.seq, "create", `创建了任务「${task.title}」`);
  return task;
}

function removeTask(space, task) {
  tasks.splice(tasks.indexOf(task), 1);
  log(space.id, task.seq, "delete", `删除了任务「${task.title}」`);
}

// ---- space mutations ----

function setRole(space, body) {
  const target = space.members.find((m) => userKey(m) === userKey(body));
  if (!target) fail(404, "成员不存在");
  if (target.role === "owner") fail(400, "不能修改所有者的角色");
  if (!["admin", "member"].includes(body.role)) fail(400, "角色无效");
  target.role = body.role;
  log(space.id, null, "role", body.role === "admin" ? `任命 ${target.nickname} 为管理员` : `撤销了 ${target.nickname} 的管理员`);
}

function rename(space, value) {
  const name = String(value ?? "").trim();
  if (!name) fail(400, "空间名不能为空");
  if ([...name].length > LIMITS.spaceName) fail(400, `空间名最多 ${LIMITS.spaceName} 字`);
  space.name = name;
  log(space.id, null, "rename", `将空间重命名为「${name}」`);
}

// ---- public board ----

function publicBoard(token) {
  const space = spaces.find((s) => s.share.enabled && s.share.token === token);
  if (!space) fail(404, "链接无效或已关闭");
  // Field whitelist: nicknames only, no IDs, no creator, no sessions, no log.
  const visible = tasks.filter((t) => t.space_id === space.id && t.status !== "archived").map((t) => ({
    seq: t.seq, title: t.title, status: t.status, assignees: t.assignees.map((a) => ({ nickname: a.nickname })),
    due_at: t.due_at, tags: t.tags, priority: t.priority, description: t.description, done_at: t.done_at, updated_at: t.updated_at,
  }));
  const updated = Math.max(space.created_at, ...visible.map((t) => t.updated_at));
  return { space: { name: space.name }, tasks: visible, updated_at: updated, server_time: nowSeconds(), timezone: TIMEZONE };
}

// ---- router ----

const META_LIMITS = { title: LIMITS.title, description: LIMITS.description, tag: LIMITS.tag, tags: LIMITS.tags, assignees: LIMITS.assignees, space_name: LIMITS.spaceName };

const routes = [
  ["GET", /^meta$/, () => ({ timezone: TIMEZONE, server_time: nowSeconds(), auto_archive_days: 7, remind_before_minutes: 30, limits: META_LIMITS })],
  ["GET", /^spaces$/, () => ({ spaces: spaces.map(summarize) })],
  ["GET", /^spaces\/(\d+)$/, (m) => detail(getSpace(m[1]))],
  ["POST", /^spaces\/(\d+)\/rename$/, (m, body) => { const s = getSpace(m[1]); rename(s, body.name); return detail(s); }],
  ["POST", /^spaces\/(\d+)\/delete$/, (m) => {
    const s = getSpace(m[1]);
    spaces.splice(spaces.indexOf(s), 1);
    [tasks, events].forEach((list) => { for (let i = list.length - 1; i >= 0; i--) if (list[i].space_id === s.id) list.splice(i, 1); });
    return { ok: true };
  }],
  ["POST", /^spaces\/(\d+)\/unbind$/, (m, body) => {
    const s = getSpace(m[1]);
    if (!s.bindings.some((b) => b.session === body.session)) fail(404, "该会话未绑定此空间");
    s.bindings = s.bindings.filter((b) => b.session !== body.session);
    log(s.id, null, "unbind", `解绑了会话 ${body.session}`);
    return detail(s);
  }],
  ["POST", /^spaces\/(\d+)\/members\/role$/, (m, body) => { const s = getSpace(m[1]); setRole(s, body); return detail(s); }],
  ["POST", /^spaces\/(\d+)\/invite\/reset$/, (m) => {
    const s = getSpace(m[1]);
    s.invite = { expires_at: nowSeconds() + INVITE_TTL_SECONDS };
    log(s.id, null, "invite", "重置了邀请码");
    return { code: randomString(INVITE_ALPHABET, 12), expires_at: s.invite.expires_at };
  }],
  ["GET", /^spaces\/(\d+)\/tasks$/, (m, params) => {
    const s = getSpace(m[1]);
    const include = [true, 1, "1"].includes(params.include_archived);
    return { tasks: tasks.filter((t) => t.space_id === s.id && (include || t.status !== "archived")).map(publicTask), server_time: nowSeconds() };
  }],
  ["POST", /^spaces\/(\d+)\/tasks$/, (m, body) => publicTask(createTask(getSpace(m[1]), body))],
  ["GET", /^spaces\/(\d+)\/tasks\/(\d+)$/, (m) => {
    const t = getTask(m[1], m[2]);
    return { task: publicTask(t), events: events.filter((e) => e.space_id === t.space_id && e.task_seq === t.seq).sort((a, b) => b.created_at - a.created_at).map(publicEvent) };
  }],
  ["POST", /^spaces\/(\d+)\/tasks\/(\d+)$/, (m, body) => {
    const s = getSpace(m[1]);
    const t = getTask(m[1], m[2]);
    const changes = applyPatch(s, t, body);
    if (Object.keys(changes).length) {
      t.updated_at = nowSeconds();
      recordChanges(t, changes);
    }
    return publicTask(t);
  }],
  ["POST", /^spaces\/(\d+)\/tasks\/(\d+)\/delete$/, (m) => { removeTask(getSpace(m[1]), getTask(m[1], m[2])); return { ok: true }; }],
  ["GET", /^spaces\/(\d+)\/events$/, (m, params) => {
    const s = getSpace(m[1]);
    const limit = Math.min(Number(params.limit) || 50, 200);
    return { events: events.filter((e) => e.space_id === s.id).sort((a, b) => b.created_at - a.created_at).slice(0, limit).map(publicEvent) };
  }],
  ["GET", /^b\/([^/]+)\/api\/board$/, (m) => publicBoard(m[1])],
];

/** @returns {Promise<any>} JSON-like response body; rejects with Error(message) carrying `.status`. */
export async function request(method, endpoint, payload = {}) {
  await new Promise((resolve) => setTimeout(resolve, LATENCY_MS));
  if (MOCK_FAIL && `${method} ${endpoint}`.includes(MOCK_FAIL)) throw new Error("模拟的服务器错误：操作失败");
  for (const [routeMethod, pattern, handler] of routes) {
    const match = routeMethod === method && pattern.exec(endpoint);
    if (match) return clone(handler(match, clone(payload)));
  }
  throw Object.assign(new Error("接口不存在"), { status: 404 });
}
