// In-memory stand-in for the plugin's REST backend. `request(method, endpoint, payload)` implements the
// contract documented at the top of api.js; replacing api.js `send()` with the AstrBot bridge is the only
// change needed in M3. Everything crosses a JSON boundary, like the real wire.
import { EVENTS, SHARE_BASE_PATH, SPACES, TASKS, TIMEZONE } from "./mock-data.js";
import { LIMITS, isOverdue, nowSeconds, userKey } from "./format.js";

const LATENCY_MS = 70;
const INVITE_TTL_SECONDS = 7 * 86400;
const INVITE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789";
const ADMIN_LABEL = "admin（WebUI）";

const spaces = clone(SPACES);
const tasks = clone(TASKS);
const events = clone(EVENTS);
let nextEventId = Math.max(...events.map((e) => e.id)) + 1;

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
const URL_SAFE = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_";

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

function shareUrl(space) {
  if (!space.share.enabled) return null;
  const url = new URL(SHARE_BASE_PATH, location.href);
  url.searchParams.set("t", space.share.token);
  return url.href;
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
    shared: space.share.enabled,
  };
}

function detail(space) {
  return {
    ...summarize(space),
    bindings: space.bindings,
    members: space.members,
    invite: space.invite ? { active: space.invite.expires_at > nowSeconds(), expires_at: space.invite.expires_at } : null,
    share: { enabled: space.share.enabled, url: shareUrl(space) },
  };
}

function log(spaceId, taskSeq, action, detailBody) {
  events.push({
    id: nextEventId++, space_id: spaceId, task_seq: taskSeq, action, detail: detailBody,
    actor: null, actor_label: ADMIN_LABEL, session: "", created_at: nowSeconds(),
  });
}

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
    if (changes.due_at) task.reminded = false;
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

function recordChanges(task, changes) {
  const { status, assignees, ...fields } = changes;
  if (status) log(task.space_id, task.seq, status[1] === "archived" ? "archive" : "status", { status });
  if (assignees) log(task.space_id, task.seq, "assign", assignees);
  if (Object.keys(fields).length) log(task.space_id, task.seq, "update", fields);
}

function createTask(space, body) {
  const task = {
    space_id: space.id,
    seq: Math.max(0, ...tasks.filter((t) => t.space_id === space.id).map((t) => t.seq), ...events.filter((e) => e.space_id === space.id).map((e) => e.task_seq)) + 1,
    title: cleanTitle(body.title),
    status: "todo",
    assignees: cleanAssignees(space, body.assignees),
    due_at: body.due_at ?? null,
    tags: cleanTags(body.tags),
    priority: cleanPriority(body.priority),
    description: cleanDescription(body.description),
    creator: { platform_id: "webui", user_id: "admin", nickname: ADMIN_LABEL },
    created_session: "",
    created_at: nowSeconds(),
    updated_at: nowSeconds(),
    done_at: null,
    reminded: false,
  };
  tasks.push(task);
  log(space.id, task.seq, "create", { title: task.title });
  return task;
}

function removeTask(space, task) {
  tasks.splice(tasks.indexOf(task), 1);
  const { seq, title, status, assignees, tags, due_at, priority, description } = task;
  log(space.id, seq, "delete", { snapshot: { seq, title, status, assignees: assignees.map((a) => a.nickname), tags, due_at, priority, description } });
}

// ---- space mutations ----

function setRole(space, body) {
  const target = space.members.find((m) => userKey(m) === userKey(body));
  if (!target) fail(404, "成员不存在");
  if (target.role === "owner") fail(400, "不能修改所有者的角色");
  if (!["admin", "member"].includes(body.role)) fail(400, "角色无效");
  target.role = body.role;
}

function setShare(space, action) {
  if (action === "on") space.share.enabled = true;
  else if (action === "off") space.share.enabled = false;
  else if (action === "reset") {
    space.share.token = randomString(URL_SAFE, 22);
    space.share.enabled = true;
  } else fail(400, "操作无效");
}

function rename(space, value) {
  const name = String(value ?? "").trim();
  if (!name) fail(400, "空间名不能为空");
  if ([...name].length > LIMITS.spaceName) fail(400, `空间名最多 ${LIMITS.spaceName} 字`);
  space.name = name;
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

const routes = [
  ["GET", /^meta$/, () => ({ timezone: TIMEZONE, server_time: nowSeconds(), auto_archive_days: 7, limits: LIMITS })],
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
    return detail(s);
  }],
  ["POST", /^spaces\/(\d+)\/members\/role$/, (m, body) => { const s = getSpace(m[1]); setRole(s, body); return detail(s); }],
  ["POST", /^spaces\/(\d+)\/invite\/reset$/, (m) => {
    const s = getSpace(m[1]);
    s.invite = { expires_at: nowSeconds() + INVITE_TTL_SECONDS };
    return { code: randomString(INVITE_ALPHABET, 12), expires_at: s.invite.expires_at };
  }],
  ["POST", /^spaces\/(\d+)\/share$/, (m, body) => { const s = getSpace(m[1]); setShare(s, body.action); return { enabled: s.share.enabled, url: shareUrl(s) }; }],
  ["GET", /^spaces\/(\d+)\/tasks$/, (m, params) => {
    const s = getSpace(m[1]);
    const include = [true, 1, "1"].includes(params.include_archived);
    return { tasks: tasks.filter((t) => t.space_id === s.id && (include || t.status !== "archived")), server_time: nowSeconds() };
  }],
  ["POST", /^spaces\/(\d+)\/tasks$/, (m, body) => createTask(getSpace(m[1]), body)],
  ["GET", /^spaces\/(\d+)\/tasks\/(\d+)$/, (m) => {
    const t = getTask(m[1], m[2]);
    return { task: t, events: events.filter((e) => e.space_id === t.space_id && e.task_seq === t.seq).sort((a, b) => b.created_at - a.created_at) };
  }],
  ["POST", /^spaces\/(\d+)\/tasks\/(\d+)$/, (m, body) => {
    const s = getSpace(m[1]);
    const t = getTask(m[1], m[2]);
    const changes = applyPatch(s, t, body);
    if (Object.keys(changes).length) {
      t.updated_at = nowSeconds();
      recordChanges(t, changes);
    }
    return t;
  }],
  ["POST", /^spaces\/(\d+)\/tasks\/(\d+)\/delete$/, (m) => { removeTask(getSpace(m[1]), getTask(m[1], m[2])); return { ok: true }; }],
  ["GET", /^spaces\/(\d+)\/events$/, (m, params) => {
    const s = getSpace(m[1]);
    const limit = Math.min(Number(params.limit) || 50, 200);
    return { events: events.filter((e) => e.space_id === s.id).sort((a, b) => b.created_at - a.created_at).slice(0, limit) };
  }],
  ["GET", /^b\/([^/]+)\/api\/board$/, (m) => publicBoard(m[1])],
];

/** @returns {Promise<any>} JSON-like response body; rejects with Error(message) carrying `.status`. */
export async function request(method, endpoint, payload = {}) {
  await new Promise((resolve) => setTimeout(resolve, LATENCY_MS));
  for (const [routeMethod, pattern, handler] of routes) {
    const match = routeMethod === method && pattern.exec(endpoint);
    if (match) return clone(handler(match, clone(payload)));
  }
  throw Object.assign(new Error("接口不存在"), { status: 404 });
}
