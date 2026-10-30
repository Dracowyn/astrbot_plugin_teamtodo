// Seed data for the in-memory mock server (mock.js). Shapes mirror docs/design.md section 7:
// users are {platform_id, user_id, nickname}; times are Unix seconds. All offsets are relative to "now"
// so the demo always shows realistic overdue / due-soon cards.
import { fromInputParts, toInputParts, nowSeconds } from "./format.js";

const NOW = Math.floor(nowSeconds());
const HOUR = 3600;
const DAY = 86400;

/** Unix seconds for `days` from today at HH:MM, in the plugin's configured time zone. */
function dueAt(days, time) {
  const [year, month, day] = toInputParts(NOW).date.split("-").map(Number);
  const shifted = new Date(Date.UTC(year, month - 1, day + days)).toISOString().slice(0, 10);
  return fromInputParts(shifted, time);
}
const ago = (hours) => NOW - Math.round(hours * HOUR);

export const TIMEZONE = "Asia/Shanghai";

export const WEBUI_USER = { platform_id: "webui", user_id: "admin", nickname: "admin" };
const user = (platform_id, user_id, nickname) => ({ platform_id, user_id, nickname });
export const USERS = {
  yutong: user("aiocqhttp", "3318052741", "林雨桐"),
  siyuan: user("aiocqhttp", "2204871956", "陈思远"),
  jiayi: user("aiocqhttp", "1893440275", "许嘉怡"),
  haoran: user("aiocqhttp", "3560912784", "王浩然"),
  zixuan: user("aiocqhttp", "2971336508", "赵梓萱"),
  mingzhe: user("aiocqhttp", "1457203396", "刘明哲"),
  zimo: user("aiocqhttp", "3102849675", "周子墨"),
  eric: user("telegram", "5128830041", "Eric"),
};

const GROUP_A = "aiocqhttp:GroupMessage:7340218659";
const GROUP_B = "aiocqhttp:GroupMessage:7348801523";
const PRIVATE_YUTONG = "aiocqhttp:FriendMessage:3318052741";

const member = (u, role, joinedDaysAgo) => ({ ...u, role, joined_at: NOW - joinedDaysAgo * DAY });

export const SPACES = [
  {
    id: 1,
    name: "计网课设小组",
    owner: USERS.yutong,
    created_at: NOW - 21 * DAY,
    bindings: [
      { session: GROUP_A, title: "计网课设 · 第三组", bound_at: NOW - 21 * DAY },
      { session: GROUP_B, title: null, bound_at: NOW - 9 * DAY },
    ],
    members: [
      member(USERS.yutong, "owner", 21),
      member(USERS.siyuan, "admin", 21),
      member(USERS.jiayi, "member", 20),
      member(USERS.haoran, "member", 20),
      member(USERS.zixuan, "member", 18),
      member(USERS.mingzhe, "member", 15),
      member(USERS.zimo, "member", 9),
      member(USERS.eric, "member", 8),
    ],
    invite: { expires_at: NOW + 5 * DAY },
    share: { enabled: true, token: "Zx3kQ8vN2mLp7RtY5wBcHa" },
  },
  {
    id: 2,
    name: "我的任务",
    owner: USERS.yutong,
    created_at: NOW - 40 * DAY,
    bindings: [{ session: PRIVATE_YUTONG, title: "林雨桐", bound_at: NOW - 40 * DAY }],
    members: [member(USERS.yutong, "owner", 40)],
    invite: null,
    share: { enabled: false, token: "Qm7dJ2xKp9VwR4tYe1NsLc" },
  },
  {
    id: 3,
    name: "社团招新筹备",
    owner: USERS.jiayi,
    created_at: NOW - 14 * DAY,
    bindings: [],
    members: [member(USERS.jiayi, "owner", 14), member(USERS.zimo, "member", 13), member(USERS.mingzhe, "member", 12)],
    invite: null,
    share: { enabled: false, token: "Hb5nT8wLc3XsP6qZy2KdRv" },
  },
];

function task(spaceId, seq, fields) {
  const createdAt = fields.created ?? ago(72);
  return {
    space_id: spaceId,
    seq,
    title: fields.title,
    status: fields.status ?? "todo",
    assignees: fields.assignees ?? [],
    due_at: fields.due ?? null,
    tags: fields.tags ?? [],
    priority: fields.priority ?? null,
    description: fields.description ?? "",
    creator: fields.creator,
    created_at: createdAt,
    updated_at: fields.updated ?? createdAt,
    done_at: fields.done ?? null,
  };
}

export const TASKS = [
  task(1, 1, { title: "确定选题：校园二手交易平台", status: "archived", assignees: [USERS.yutong, USERS.siyuan], tags: ["选题"], creator: USERS.yutong, created: NOW - 20 * DAY, done: NOW - 12 * DAY, updated: NOW - 5 * DAY }),
  task(1, 2, { title: "需求分析文档初稿", status: "done", assignees: [USERS.siyuan], tags: ["文档"], priority: "medium", creator: USERS.yutong, created: NOW - 16 * DAY, done: ago(5 * 24 + 3), updated: ago(5 * 24 + 3) }),
  task(1, 3, {
    title: "数据库 E-R 图设计", status: "doing", assignees: [USERS.siyuan, USERS.haoran], tags: ["后端", "文档"], priority: "high", due: dueAt(2, "18:00"),
    creator: USERS.yutong, created: ago(6 * 24), updated: ago(3),
    description: "按第三范式设计，至少包含：\n- 用户 / 商品 / 订单 / 评价\n- 校内认证（学号绑定）\n- 商品图片另存对象表，不要塞进商品表\n\n画图工具不限，推荐 https://app.diagrams.net/ 。\n评审会：周五 19:00，腾讯会议 https://meeting.tencent.com/dm/Kq83mZ0vXo2h",
  }),
  task(1, 4, { title: "登录注册接口（含学号校验）", status: "doing", assignees: [USERS.haoran], tags: ["后端"], priority: "high", due: dueAt(-1, "18:00"), creator: USERS.siyuan, created: ago(5 * 24), updated: ago(20) }),
  task(1, 5, {
    title: "前端首页布局", assignees: [USERS.jiayi], tags: ["前端"], priority: "medium", due: dueAt(5, "12:00"), creator: USERS.yutong, created: ago(4 * 24), updated: ago(30),
    description: "页面参考：\nhttps://www.figma.com/file/aB3xK9/campus-market?node-id=12%3A34\n\n要求：\n1. 首页展示最新上架商品（卡片流）\n2. 顶部搜索框，支持按分类筛选\n3. 适配手机，宽度 360px 起\n\n组件库统一用 Element Plus：https://element-plus.org/zh-CN/ ，不要混用别的。\n接口文档见 http://192.168.1.20:8080/docs（内网，需连校园网）\n\n<b>这行里的标签不会被当成 HTML 渲染</b>",
  }),
  task(1, 6, { title: "搭建 Docker 开发环境", status: "done", assignees: [USERS.mingzhe], tags: ["后端", "运维"], creator: USERS.siyuan, created: ago(8 * 24), done: ago(2 * 24), updated: ago(2 * 24) }),
  task(1, 7, { title: "编写接口测试用例", tags: ["后端", "测试"], priority: "low", creator: USERS.siyuan, created: ago(3 * 24) }),
  task(1, 8, { title: "答辩 PPT 框架", assignees: [USERS.zixuan, USERS.yutong], tags: ["文档"], priority: "medium", due: dueAt(9, "09:00"), creator: USERS.zixuan, session: GROUP_B, created: ago(2 * 24) }),
  task(1, 10, { title: "部署到校内服务器并配置域名", assignees: [USERS.mingzhe], tags: ["运维"], priority: "high", due: dueAt(-2, "20:00"), creator: USERS.yutong, created: ago(7 * 24), updated: ago(40) }),
  task(1, 11, { title: "商品搜索与分页", status: "doing", assignees: [USERS.jiayi, USERS.eric], tags: ["前端", "后端"], priority: "medium", due: dueAt(3, "21:00"), creator: USERS.siyuan, session: GROUP_B, created: ago(2 * 24), updated: ago(6) }),
  task(1, 12, { title: "整理参考文献格式", status: "done", assignees: [USERS.zixuan], tags: ["文档"], creator: USERS.zixuan, created: ago(4 * 24), done: ago(26), updated: ago(26) }),
  task(1, 14, { title: "补充压力测试报告", assignees: [USERS.mingzhe], tags: ["测试"], priority: "low", due: dueAt(6, "18:00"), creator: WEBUI_USER, created: ago(10) }),
  task(1, 13, { title: "周会纪要 10-26", status: "archived", tags: ["会议"], creator: USERS.yutong, created: NOW - 5 * DAY, done: NOW - 4 * DAY, updated: NOW - 2 * DAY }),

  task(2, 1, { title: "交《操作系统》实验报告", status: "doing", assignees: [USERS.yutong], priority: "high", due: dueAt(0, "23:59"), creator: USERS.yutong, session: PRIVATE_YUTONG, created: ago(50), updated: ago(5) }),
  task(2, 2, { title: "给妈妈打电话", assignees: [USERS.yutong], due: dueAt(1, "20:00"), creator: USERS.yutong, session: PRIVATE_YUTONG, created: ago(20) }),
  task(2, 3, { title: "买四级报名资料", status: "done", assignees: [USERS.yutong], creator: USERS.yutong, session: PRIVATE_YUTONG, created: ago(90), done: ago(30), updated: ago(30) }),
  task(2, 4, { title: "整理 Obsidian 笔记", assignees: [USERS.yutong], tags: ["整理"], priority: "low", creator: USERS.yutong, session: PRIVATE_YUTONG, created: ago(8 * 24) }),

  task(3, 1, { title: "定海报设计稿", status: "doing", assignees: [USERS.jiayi], tags: ["宣传"], priority: "high", due: dueAt(1, "18:00"), creator: USERS.jiayi, session: GROUP_B, created: ago(4 * 24), updated: ago(12) }),
  task(3, 2, {
    title: "申请活动场地：学生活动中心 203", assignees: [USERS.zimo], tags: ["场地"], due: dueAt(3, "17:00"), creator: USERS.jiayi, session: GROUP_B, created: ago(4 * 24),
    description: "申请表模板：https://example.edu.cn/student-affairs/forms/venue-apply.docx\n需要辅导员签字后交到 B 栋 305。",
  }),
  task(3, 3, { title: "招新问卷", status: "done", assignees: [USERS.mingzhe], tags: ["宣传"], creator: USERS.jiayi, session: GROUP_B, created: ago(6 * 24), done: ago(3 * 24), updated: ago(3 * 24) }),
  task(3, 4, { title: "采购易拉宝和桌布", assignees: [USERS.mingzhe], tags: ["物资"], priority: "medium", due: dueAt(-1, "12:00"), creator: USERS.jiayi, session: GROUP_B, created: ago(5 * 24), updated: ago(30) }),
];

// Operation log, newest-first on the wire. Shape: {kind: "task"|"space", task_seq: number|null, action, summary,
// actor_name, created_at}. The server renders `summary` and `actor_name`; the UI never parses anything else.
const event = (task_seq, action, summary, actor_name, hoursAgo) => ({
  space_id: 1, kind: task_seq == null ? "space" : "task", task_seq, action, summary, actor_name, created_at: ago(hoursAgo),
});
const spaceEvent = (space_id, action, summary, actor_name, hoursAgo) => ({ ...event(null, action, summary, actor_name, hoursAgo), space_id });
const otherEvent = (space_id, task_seq, action, summary, actor_name, hoursAgo) => ({ ...event(task_seq, action, summary, actor_name, hoursAgo), space_id });

export const EVENTS = [
  event(3, "create", "创建了任务「数据库 E-R 图设计」", "林雨桐", 6 * 24),
  event(3, "assign", "指派给 陈思远", "林雨桐", 6 * 24),
  event(3, "assign", "指派给 王浩然", "陈思远", 4 * 24),
  event(3, "status", "将状态由 待办 改为 进行中", "陈思远", 3 * 24),
  event(3, "update", "设置了优先级 高和截止时间", "林雨桐", 26),
  event(3, "update", "截止时间已调整", "陈思远", 3),
  event(4, "create", "创建了任务「登录注册接口（含学号校验）」", "陈思远", 5 * 24),
  event(4, "status", "将状态由 待办 改为 进行中", "王浩然", 3 * 24 + 5),
  event(4, "update", "标题改为「登录注册接口（含学号校验）」", "陈思远", 20),
  event(6, "status", "将状态由 进行中 改为 已完成", "刘明哲", 2 * 24),
  event(9, "delete", "删除了任务「重复的任务：画原型图」", "陈思远", 3 * 24 + 8),
  event(10, "create", "创建了任务「部署到校内服务器并配置域名」", "林雨桐", 7 * 24),
  event(11, "create", "创建了任务「商品搜索与分页」", "陈思远", 2 * 24),
  event(11, "status", "将状态由 待办 改为 进行中", "许嘉怡", 6),
  event(12, "status", "将状态由 进行中 改为 已完成", "赵梓萱", 26),
  event(1, "archive", "任务完成满期，已自动归档", "系统", 5 * 24),
  event(13, "archive", "归档了任务", "林雨桐", 2 * 24),
  event(14, "create", "创建了任务「补充压力测试报告」", "admin（WebUI）", 10),
  spaceEvent(1, "bind", "绑定了会话「计网课设 · 第三组」", "林雨桐", 21 * 24),
  spaceEvent(1, "rename", "将空间重命名为「计网课设小组」", "林雨桐", 12 * 24),
  spaceEvent(1, "role", "任命 陈思远 为管理员", "林雨桐", 11 * 24),
  otherEvent(2, 1, "status", "将状态由 待办 改为 进行中", "林雨桐", 5),
  otherEvent(3, 4, "update", "优先级 无 → 中", "许嘉怡", 30),
];
