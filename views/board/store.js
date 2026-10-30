// Admin view state and actions. One reactive object; components read `state` and call actions.
// All server access goes through api.js. A failure is shown next to the action that caused it
// (`state.errors[scope]`, rendered by InlineError) or, for actions without a place of their own, as a toast.
// Nothing is swallowed and the UI is never left half-updated.
import { computed, reactive } from "./vue.js";
import * as api from "./api.js";
import { applyLimits, setTimeZone, nowSeconds } from "./format.js";

const TOAST_MS = 4500;

export const state = reactive({
  ready: false,
  fatal: "",
  now: nowSeconds(),
  meta: null,
  spaces: [],
  spaceId: null,
  space: null, // SpaceDetail
  loadingSpace: false,
  spaceError: "",
  tasks: [],
  view: "board", // board | log | settings
  showArchived: false,
  filters: { assignee: "", tag: "" },
  drawer: null, // { seq, task, events, loadingEvents }
  events: [], // space-wide log
  syncResult: null, // { added, results } of the last "sync members from groups" run; cleared on space switch
  freshInviteCode: null, // { spaceId, code, expires_at }: shown once, never refetched
  creating: null, // status the create panel adds to ("todo" | "doing" | "done"), null when closed
  sidebarOpen: false, // mobile drawer
  errors: {}, // scope -> message of the last failed action in that scope
  pending: {}, // scope -> true while a call is in flight
  toasts: [],
  confirm: null, // { title, message, confirmLabel, requireText, danger, resolve }
});

let toastId = 0;

export function notify(message, kind = "info") {
  const id = ++toastId;
  state.toasts.push({ id, message, kind });
  setTimeout(() => dismissToast(id), TOAST_MS);
}
export function dismissToast(id) {
  const index = state.toasts.findIndex((toast) => toast.id === id);
  if (index >= 0) state.toasts.splice(index, 1);
}

/**
 * Runs an action. With a `scope`, the in-flight flag and the failure message live in
 * state.pending[scope] / state.errors[scope] (shown inline next to the action); without one the failure becomes
 * a toast. Returns undefined on failure.
 */
async function attempt(action, scope) {
  if (scope) {
    state.errors[scope] = "";
    state.pending[scope] = true;
  }
  try {
    return await action();
  } catch (err) {
    const message = api.errorText(err);
    if (scope) state.errors[scope] = message;
    else notify(message, "error");
    return undefined;
  } finally {
    if (scope) state.pending[scope] = false;
  }
}

/** Promise-based confirmation rendered by ConfirmHost. Resolves true when confirmed. */
export function ask(options) {
  return new Promise((resolve) => {
    state.confirm = { confirmLabel: "确认", danger: false, requireText: "", ...options, resolve };
  });
}
export function answer(result) {
  const pending = state.confirm;
  state.confirm = null;
  pending?.resolve(result);
}

// ---- derived ----

export const members = computed(() => state.space?.members ?? []);
export const boardTasks = computed(() => state.tasks.filter((task) => state.showArchived || task.status !== "archived"));
export const allTags = computed(() => [...new Set(state.tasks.flatMap((task) => task.tags))].sort((a, b) => a.localeCompare(b, "zh-CN")));
export const visibleTasks = computed(() => {
  const { assignee, tag } = state.filters;
  return boardTasks.value.filter(
    (task) =>
      (!assignee || task.assignees.some((a) => `${a.platform_id}:${a.user_id}` === assignee)) && (!tag || task.tags.includes(tag)),
  );
});
export const hasFilters = computed(() => Boolean(state.filters.assignee || state.filters.tag));

// ---- loading ----

export async function init() {
  try {
    state.meta = await api.getMeta();
    applyLimits(state.meta.limits);
    setTimeZone(state.meta.timezone);
    await refreshSpaces();
    state.ready = true;
    const first = state.spaces[0];
    if (first) await selectSpace(first.id);
  } catch (err) {
    state.fatal = api.errorText(err);
  }
  setInterval(() => { state.now = nowSeconds(); }, 30000);
}

export async function refreshSpaces() {
  state.spaces = (await api.listSpaces()).spaces;
}

/** Loads the open space and its task list together; drops the result if another space was opened meanwhile. */
async function reloadSpace() {
  const id = state.spaceId;
  const [space, list] = await Promise.all([api.getSpace(id), api.listTasks(id, state.showArchived)]);
  if (state.spaceId !== id) return;
  state.space = space;
  state.tasks = list.tasks;
  state.now = list.server_time;
}

export async function selectSpace(id, view = "board") {
  state.spaceId = id;
  state.view = view;
  state.drawer = null;
  state.space = null;
  state.tasks = [];
  state.events = [];
  state.errors = {};
  state.creating = null;
  state.sidebarOpen = false;
  state.spaceError = "";
  state.freshInviteCode = null;
  state.syncResult = null;
  state.filters = { assignee: "", tag: "" };
  state.loadingSpace = true;
  try {
    await reloadSpace();
    if (state.spaceId === id && view === "log") await loadEvents();
  } catch (err) {
    if (state.spaceId === id) state.spaceError = api.errorText(err);
  } finally {
    if (state.spaceId === id) state.loadingSpace = false;
  }
}

/** Retry after a failed space load. */
export const retrySpace = () => selectSpace(state.spaceId, state.view);

export async function setView(view) {
  state.view = view;
  if (view !== "settings") state.freshInviteCode = null; // the plain code is gone once the settings page is left
  if (view === "log") await loadEvents();
}

export async function loadEvents() {
  const result = await attempt(() => api.listEvents(state.spaceId), "log");
  if (result) state.events = result.events;
}

export async function setShowArchived(value) {
  const list = await attempt(() => api.listTasks(state.spaceId, value), "board");
  if (!list) return;
  state.showArchived = value;
  state.tasks = list.tasks;
}

/** Refreshes list counters and the open space after a mutation. */
async function afterMutation() {
  await Promise.all([refreshSpaces(), reloadSpace()]);
}

// ---- tasks ----

/** Opens the create panel for a status; clicking the same entry again closes it. */
export function startCreate(status = "todo") {
  state.errors.create = "";
  state.creating = state.creating === status && state.view === "board" ? null : status;
  state.view = "board";
}
export const stopCreate = () => { state.creating = null; };
export const toggleSidebar = (open = !state.sidebarOpen) => { state.sidebarOpen = open; };

/** The create endpoint has no status field, so a non-todo target is applied with a follow-up update. */
export async function createTask(fields, status = "todo") {
  const task = await attempt(async () => {
    const created = await api.createTask(state.spaceId, fields);
    if (status !== "todo") await api.updateTask(state.spaceId, created.seq, { status });
    return created;
  }, "create");
  if (!task) return false;
  await attempt(afterMutation, "create");
  notify(`已创建 #${task.seq}`);
  return true;
}

/** Optimistic status change used by drag and drop; rolls back (with a visible message) when the server refuses. */
export async function moveTask(seq, status) {
  const task = state.tasks.find((t) => t.seq === seq);
  if (!task || task.status === status) return;
  const previous = { status: task.status, done_at: task.done_at, overdue: task.overdue, updated_at: task.updated_at };
  task.status = status;
  task.done_at = status === "done" ? nowSeconds() : null;
  try {
    Object.assign(task, await api.updateTask(state.spaceId, seq, { status }));
  } catch (err) {
    Object.assign(task, previous);
    notify(`#${seq} 状态未修改，已还原：${api.errorText(err)}`, "error");
    return;
  }
  await attempt(refreshSpaces);
}

/** `patch` carries only the changed fields; null clears due_at/priority, lists replace wholesale. */
export async function saveTask(seq, patch) {
  const saved = await attempt(() => api.updateTask(state.spaceId, seq, patch), "drawer");
  if (!saved) return false;
  await attempt(afterMutation, "drawer");
  if (state.drawer?.seq === seq) await loadDrawerEvents();
  notify("已保存");
  return true;
}

export async function removeTask(seq) {
  const done = await attempt(() => api.deleteTask(state.spaceId, seq), "drawer");
  if (!done) return false;
  state.drawer = null;
  await attempt(afterMutation);
  notify(`已删除 #${seq}`);
  return true;
}

// ---- drawer ----

async function loadDrawerEvents() {
  const seq = state.drawer?.seq;
  if (seq == null) return;
  state.drawer.loadingEvents = true;
  const result = await attempt(() => api.getTask(state.spaceId, seq), "drawerLog");
  if (state.drawer?.seq !== seq) return;
  state.drawer.loadingEvents = false;
  if (result) {
    state.drawer.task = result.task;
    state.drawer.events = result.events;
  }
}

export async function openTask(seq) {
  const task = state.tasks.find((t) => t.seq === seq);
  if (!task) return;
  state.errors.drawer = "";
  state.errors.drawerLog = "";
  state.drawer = { seq, task, events: [], loadingEvents: true };
  await loadDrawerEvents();
}
export const closeTask = () => { state.drawer = null; };

// ---- space settings (each concern has its own error scope, shown in its row) ----

export async function renameSpace(name) {
  const space = await attempt(() => api.renameSpace(state.spaceId, name), "rename");
  if (!space) return false;
  state.space = space;
  await attempt(refreshSpaces, "rename");
  notify("已重命名");
  return true;
}

export async function unbind(session) {
  const space = await attempt(() => api.unbindSession(state.spaceId, session), "bindings");
  if (!space) return;
  state.space = space;
  await attempt(refreshSpaces, "bindings");
  notify("已解绑会话");
}

export async function setRole(user, role) {
  const space = await attempt(() => api.setMemberRole(state.spaceId, user, role), "members");
  if (!space) return;
  state.space = space;
  notify(role === "admin" ? `已任命 ${user.nickname} 为管理员` : `已撤销 ${user.nickname} 的管理员`);
}

/** Pulls the member lists of the bound QQ groups; the response carries the refreshed SpaceDetail. */
export async function syncMembers() {
  const id = state.spaceId;
  state.syncResult = null;
  const result = await attempt(() => api.syncMembers(id), "sync");
  if (!result || state.spaceId !== id) return;
  state.space = result.detail;
  state.syncResult = { added: result.added, results: result.results };
  await attempt(refreshSpaces, "sync");
}

/** The plain code exists only in this response; it is kept in memory for the current settings page only. */
export async function resetInvite() {
  const result = await attempt(() => api.resetInvite(state.spaceId), "invite");
  if (!result) return;
  state.freshInviteCode = { spaceId: state.spaceId, ...result };
  const space = await attempt(() => api.getSpace(state.spaceId), "invite");
  if (space) state.space = space;
}

/** Public link: action is "on" | "reset" | "off"; the server answers with the fresh SpaceDetail. */
export async function shareSpace(action) {
  const space = await attempt(() => api.shareSpace(state.spaceId, action), "share");
  if (!space) return;
  state.space = space;
  notify({ on: "已开启公开链接", reset: "已重置公开链接", off: "已关闭分享" }[action]);
}

export async function removeSpace() {
  const gone = await attempt(() => api.deleteSpace(state.spaceId), "delete");
  if (!gone) return;
  notify("已删除空间");
  const next = await attempt(async () => {
    await refreshSpaces();
    return state.spaces[0];
  });
  state.space = null;
  state.tasks = [];
  if (next) await selectSpace(next.id);
  else state.spaceId = null;
}
