// Pure helpers shared by the admin view and the public board: labels, time formatting, ordering,
// identity keys and the safe link tokenizer. No DOM access here.

export const STATUSES = [
  { key: "todo", label: "待办" },
  { key: "doing", label: "进行中" },
  { key: "done", label: "已完成" },
];
export const STATUS_LABEL = { todo: "待办", doing: "进行中", done: "已完成", archived: "已归档" };
export const PRIORITY_LABEL = { high: "高", medium: "中", low: "低" };
export const PRIORITY_RANK = { high: 0, medium: 1, low: 2 };
export const ROLE_LABEL = { owner: "所有者", admin: "管理员", member: "成员" };

// Limits mirror core/models.py (MAX_*), enforced by the backend for every entry point.
export const LIMITS = { title: 200, description: 4000, tag: 20, tags: 10, assignees: 20, spaceName: 30 };

export const nowSeconds = () => Date.now() / 1000;

/** Identity key: "platform_id:user_id" (UserRef.key); falls back to the nickname on the public board. */
export const userKey = (user) => (user.platform_id ? `${user.platform_id}:${user.user_id}` : user.nickname);

/** Overdue = due date passed and the task is still todo/doing. */
export function isOverdue(task, now = nowSeconds()) {
  return task.due_at != null && task.due_at < now && (task.status === "todo" || task.status === "doing");
}

// ---- time zone aware formatting (the plugin stores Unix seconds; display uses the configured zone) ----

let timeZone = "Asia/Shanghai";
const formatters = new Map();

export function setTimeZone(zone) {
  if (!zone || zone === timeZone) return;
  timeZone = zone;
  formatters.clear();
}

function wallParts(seconds) {
  let formatter = formatters.get(timeZone);
  if (!formatter) {
    formatter = new Intl.DateTimeFormat("en-CA", {
      timeZone, hourCycle: "h23", year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit",
    });
    formatters.set(timeZone, formatter);
  }
  const parts = {};
  for (const part of formatter.formatToParts(new Date(seconds * 1000))) parts[part.type] = part.value;
  return { year: +parts.year, month: +parts.month, day: +parts.day, hour: +parts.hour, minute: +parts.minute };
}

const pad = (value) => String(value).padStart(2, "0");
const dayIndex = (p) => Math.floor(Date.UTC(p.year, p.month - 1, p.day) / 86400000);

/** "2026-10-30 14:05" in the configured zone. */
export function formatDateTime(seconds) {
  const p = wallParts(seconds);
  return `${p.year}-${pad(p.month)}-${pad(p.day)} ${pad(p.hour)}:${pad(p.minute)}`;
}

/** Compact due label: 今天 18:00 / 明天 09:30 / 昨天 18:00 / 11-05 18:00 / 2027-01-04 18:00. */
export function formatDue(seconds, now = nowSeconds()) {
  const p = wallParts(seconds);
  const time = `${pad(p.hour)}:${pad(p.minute)}`;
  const today = wallParts(now);
  const diff = dayIndex(p) - dayIndex(today);
  if (diff === 0) return `今天 ${time}`;
  if (diff === 1) return `明天 ${time}`;
  if (diff === -1) return `昨天 ${time}`;
  const date = `${pad(p.month)}-${pad(p.day)}`;
  return p.year === today.year ? `${date} ${time}` : `${p.year}-${date} ${time}`;
}

/** Relative time for logs and "updated" stamps. */
export function formatRelative(seconds, now = nowSeconds()) {
  const delta = now - seconds;
  if (delta < 45) return "刚刚";
  if (delta < 3600) return `${Math.round(delta / 60)} 分钟前`;
  if (delta < 6 * 3600) return `${Math.round(delta / 3600)} 小时前`;
  const p = wallParts(seconds);
  const time = `${pad(p.hour)}:${pad(p.minute)}`;
  const diff = dayIndex(wallParts(now)) - dayIndex(p);
  if (diff === 0) return `今天 ${time}`;
  if (diff === 1) return `昨天 ${time}`;
  return formatDateTime(seconds).slice(5);
}

/** Splits a stored timestamp into the values of <input type=date> and <input type=time>. */
export function toInputParts(seconds) {
  if (seconds == null) return { date: "", time: "" };
  const p = wallParts(seconds);
  return { date: `${p.year}-${pad(p.month)}-${pad(p.day)}`, time: `${pad(p.hour)}:${pad(p.minute)}` };
}

/** Inverse of toInputParts; an empty date means "no due time". A date without a time defaults to 23:59. */
export function fromInputParts(date, time) {
  if (!date) return null;
  const [year, month, day] = date.split("-").map(Number);
  const [hour, minute] = (time || "23:59").split(":").map(Number);
  const guess = Date.UTC(year, month - 1, day, hour, minute) / 1000;
  const wall = wallParts(guess);
  const wallAsUtc = Date.UTC(wall.year, wall.month - 1, wall.day, wall.hour, wall.minute) / 1000;
  return guess - (wallAsUtc - guess);
}

// ---- ordering ----

/** Board order inside a column: priority, then due time, then short number. Done: latest first. */
export function sortTasks(tasks, status) {
  const rank = (task) => (task.priority ? PRIORITY_RANK[task.priority] : 3);
  const due = (task) => (task.due_at == null ? Infinity : task.due_at);
  return [...tasks].sort((a, b) => {
    if (status === "done") return (b.done_at ?? 0) - (a.done_at ?? 0) || b.seq - a.seq;
    return rank(a) - rank(b) || due(a) - due(b) || a.seq - b.seq;
  });
}

// ---- safe link tokenizer: only http(s) URLs become links, everything else stays text ----

const URL_PATTERN = /https?:\/\/[^\s<>"'`，。；：！？、（）《》【】“”‘’]+/g;
const TRAILING_PUNCT = /[.,;:!?)\]}]+$/;

/** @returns {{type: "text"|"link", value: string}[]} */
export function splitLinks(text) {
  const tokens = [];
  let last = 0;
  for (const match of text.matchAll(URL_PATTERN)) {
    const url = match[0].replace(TRAILING_PUNCT, "");
    if (url.length <= "https://".length) continue;
    if (match.index > last) tokens.push({ type: "text", value: text.slice(last, match.index) });
    tokens.push({ type: "link", value: url });
    last = match.index + url.length;
  }
  if (last < text.length) tokens.push({ type: "text", value: text.slice(last) });
  return tokens;
}

// ---- sessions ----

const SESSION_KIND = { GroupMessage: "群聊", FriendMessage: "私聊" };

/** Splits a unified_msg_origin ("<platform>:<GroupMessage|FriendMessage>:<id>") for display. */
export function describeSession(session) {
  const [platform = "", kind = "", ...rest] = session.split(":");
  const id = rest.join(":");
  return { platform, id, kind: SESSION_KIND[kind] ?? "会话", text: `${SESSION_KIND[kind] ?? "会话"} ${id}` };
}
