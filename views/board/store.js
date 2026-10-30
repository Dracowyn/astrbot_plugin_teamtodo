// Admin view state and actions. One reactive object; components read `state` and call actions.
// All server access goes through api.js. Failures surface as toasts and never leave the UI half-updated.
import { computed, reactive } from "./vue.js";
import * as api from "./api.js";
import { setTimeZone, nowSeconds } from "./format.js";

const TOAST_MS = 4500;

export const state = reactive({
  ready: false,
  fatal: "",
  now: nowSeconds(),
  meta: null,
  spaces: [],
  spaceId: null,
  space: null, // SpaceDetail
  tasks: [],
  view: "board", // board | log | settings
  showArchived: false,
  filters: { assignee: "", tag: "" },
  drawer: null, // { seq, task, events, loadingEvents }
  events: [], // space-wide log
  freshInviteCode: null, // { spaceId, code, expires_at }: shown once, never refetched
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

/** Runs an action; on failure shows the message as a toast and returns undefined. */
async function attempt(action) {
  try {
    return await action();
  } catch (err) {
    notify(api.errorText(err), "error");
    return undefined;
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
    setTimeZone(state.meta.timezone);
    await refreshSpaces();
    const first = state.spaces[0];
    if (first) await selectSpace(first.id);
    state.ready = true;
  } catch (err) {
    state.fatal = api.errorText(err);
  }
  setInterval(() => { state.now = nowSeconds(); }, 30000);
}

export async function refreshSpaces() {
  state.spaces = (await api.listSpaces()).spaces;
}

async function reloadSpace() {
  const [space, list] = await Promise.all([api.getSpace(state.spaceId), api.listTasks(state.spaceId, state.showArchived)]);
  state.space = space;
  state.tasks = list.tasks;
  state.now = list.server_time;
}

export async function selectSpace(id, view = "board") {
  state.spaceId = id;
  state.view = view;
  state.drawer = null;
  state.events = [];
  state.filters = { assignee: "", tag: "" };
  await attempt(reloadSpace);
  if (view === "log") await loadEvents();
}

export async function setView(view) {
  state.view = view;
  if (view === "log") await loadEvents();
}

export async function loadEvents() {
  const result = await attempt(() => api.listEvents(state.spaceId));
  if (result) state.events = result.events;
}

export async function setShowArchived(value) {
  state.showArchived = value;
  await attempt(async () => { state.tasks = (await api.listTasks(state.spaceId, value)).tasks; });
}

/** Refreshes list counters and the open space after a mutation. */
async function afterMutation() {
  await Promise.all([refreshSpaces(), reloadSpace()]);
}

// ---- tasks ----

export async function createTask(fields) {
  const task = await attempt(() => api.createTask(state.spaceId, fields));
  if (!task) return false;
  await attempt(afterMutation);
  notify(`已创建 #${task.seq}`);
  return true;
}

/** Optimistic status change used by drag and drop; rolls back when the server refuses. */
export async function moveTask(seq, status) {
  const task = state.tasks.find((t) => t.seq === seq);
  if (!task || task.status === status) return;
  const previous = { status: task.status, done_at: task.done_at };
  task.status = status;
  task.done_at = status === "done" ? nowSeconds() : null;
  try {
    const saved = await api.updateTask(state.spaceId, seq, { status });
    Object.assign(task, saved);
    await refreshSpaces();
  } catch (err) {
    Object.assign(task, previous);
    notify(api.errorText(err), "error");
  }
}

export async function saveTask(seq, patch) {
  const saved = await attempt(() => api.updateTask(state.spaceId, seq, patch));
  if (!saved) return false;
  await attempt(afterMutation);
  if (state.drawer?.seq === seq) await loadDrawerEvents();
  notify("已保存");
  return true;
}

export async function removeTask(seq) {
  const done = await attempt(() => api.deleteTask(state.spaceId, seq));
  if (!done) return false;
  state.drawer = null;
  await attempt(afterMutation);
  notify(`已删除 #${seq}`);
  return true;
}

// ---- drawer ----

async function loadDrawerEvents() {
  const seq = state.drawer?.seq;
  const result = await attempt(() => api.getTask(state.spaceId, seq));
  if (result && state.drawer?.seq === seq) {
    state.drawer.task = result.task;
    state.drawer.events = result.events;
  }
}

export async function openTask(seq) {
  const task = state.tasks.find((t) => t.seq === seq);
  if (!task) return;
  state.drawer = { seq, task, events: [] };
  await loadDrawerEvents();
}
export const closeTask = () => { state.drawer = null; };

// ---- space settings ----

export async function renameSpace(name) {
  const space = await attempt(() => api.renameSpace(state.spaceId, name));
  if (!space) return false;
  state.space = space;
  await refreshSpaces();
  notify("已重命名");
  return true;
}

export async function unbind(session) {
  const space = await attempt(() => api.unbindSession(state.spaceId, session));
  if (!space) return;
  state.space = space;
  await refreshSpaces();
  notify("已解绑会话");
}

export async function setRole(user, role) {
  const space = await attempt(() => api.setMemberRole(state.spaceId, user, role));
  if (!space) return;
  state.space = space;
  notify(role === "admin" ? `已任命 ${user.nickname} 为管理员` : `已撤销 ${user.nickname} 的管理员`);
}

export async function resetInvite() {
  const result = await attempt(() => api.resetInvite(state.spaceId));
  if (!result) return;
  state.freshInviteCode = { spaceId: state.spaceId, ...result };
  state.space = await api.getSpace(state.spaceId);
}

export async function changeShare(action) {
  const result = await attempt(() => api.setShare(state.spaceId, action));
  if (!result) return;
  state.space.share = result;
  await refreshSpaces();
  notify({ on: "已开启公开链接", off: "已关闭公开链接", reset: "已重置公开链接，旧链接立即失效" }[action]);
}

export async function removeSpace() {
  const gone = await attempt(() => api.deleteSpace(state.spaceId));
  if (!gone) return;
  notify("已删除空间");
  await refreshSpaces();
  state.space = null;
  state.tasks = [];
  const next = state.spaces[0];
  if (next) await selectSpace(next.id);
  else state.spaceId = null;
}
