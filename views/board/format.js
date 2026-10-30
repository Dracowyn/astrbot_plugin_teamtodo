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
export const STATUS_COLOR = { todo: "gray", doing: "yellow", done: "green", archived: "brown" };
export const PRIORITY_COLOR = { high: "red", medium: "yellow", low: "gray" };
export const ROLE_COLOR = { owner: "orange", admin: "blue", member: "gray" };
// Red is reserved for priority 高 and overdue, so avatars never use it.
const NAME_COLORS = ["blue", "green", "purple", "orange", "pink", "brown", "yellow", "gray"];
const TONE_COUNT = 8; // matches the .tone-0 ... .tone-7 classes in tokens.css

function hashString(text) {
  let hash = 0;
  for (const char of String(text)) hash = (hash * 31 + char.codePointAt(0)) >>> 0;
  return hash;
}
/** Deterministic palette colour for an avatar, picked from the person's name. */
export const avatarColor = (name) => NAME_COLORS[hashString(name) % NAME_COLORS.length];
/** CSS class of a space's tone (cover colour and icon tile). Derived from the name so the admin and public pages agree. */
export const toneClass = (name) => `tone-${hashString(name) % TONE_COUNT}`;
/** First visible character, upper-cased (used by avatars and page icons). */
export const initial = (name) => [...String(name ?? "").trim()][0]?.toUpperCase() ?? "?";
export const ROLE_LABEL = { owner: "所有者", admin: "管理员", member: "成员" };

// Input limits. These defaults are replaced by `meta.limits` from the server (applyLimits) before the admin
// page renders; the server enforces them for every entry point.
export const LIMITS = { title: 200, description: 4000, tag: 20, tags: 10, assignees: 20, spaceName: 30 };

/** Applies `meta.limits` ({title, description, tag, tags, assignees, space_name}); unknown keys are ignored. */
export function applyLimits(limits = {}) {
  const { space_name: spaceName, ...rest } = limits;
  for (const [key, value] of Object.entries({ ...rest, spaceName })) {
    if (key in LIMITS && Number.isInteger(value) && value > 0) LIMITS[key] = value;
  }
}

export const nowSeconds = () => Date.now() / 1000;

/** Identity key: "platform_id:user_id" (UserRef.key); falls back to the nickname on the public board. */
export const userKey = (user) => (user.platform_id ? `${user.platform_id}:${user.user_id}` : user.nickname);

/** Client-side overdue, used only by the public board (the admin API sends a server-computed `overdue`). */
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

/**
 * Relative due label for the card chip. Open tasks read "已逾期 1 天", "今天 18:00", "2 天后 18:00"; `tone` is
 * "overdue", "soon" (due within 24 hours) or "". Closed tasks fall back to the plain label with no tone.
 */
export function describeDue(seconds, now = nowSeconds(), { open = true, overdue = false } = {}) {
  if (!open) return { text: formatDue(seconds, now), tone: "" };
  if (overdue) {
    const late = Math.max(0, now - seconds);
    const text = late < 3600 ? "不到 1 小时" : late < 86400 ? `${Math.floor(late / 3600)} 小时` : `${Math.floor(late / 86400)} 天`;
    return { text: `已逾期 ${text}`, tone: "overdue" };
  }
  const p = wallParts(seconds);
  const diff = dayIndex(p) - dayIndex(wallParts(now));
  const tone = seconds - now < 86400 ? "soon" : "";
  if (diff >= 2 && diff <= 30) return { text: `${diff} 天后 ${pad(p.hour)}:${pad(p.minute)}`, tone };
  return { text: formatDue(seconds, now), tone };
}

/** Day heading for grouped logs: 今天 / 昨天 / 10月28日 / 2025年12月3日. */
export function formatDayHeading(seconds, now = nowSeconds()) {
  const p = wallParts(seconds);
  const today = wallParts(now);
  const diff = dayIndex(today) - dayIndex(p);
  if (diff === 0) return "今天";
  if (diff === 1) return "昨天";
  return p.year === today.year ? `${p.month}月${p.day}日` : `${p.year}年${p.month}月${p.day}日`;
}

/** "HH:mm" in the configured zone. */
export const formatClock = (seconds) => formatDateTime(seconds).slice(11);

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
