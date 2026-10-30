// Operation log list. Used inside the task drawer (per task) and on the space-wide log tab.
// Every entry: who, when, from which session, what changed. Text is built from plain strings only.
import { PRIORITY_LABEL, STATUS_LABEL, describeSession, formatDateTime, formatRelative } from "../format.js";

const dueText = (value) => (value == null ? "无" : formatDateTime(value));
const priorityText = (value) => (value ? PRIORITY_LABEL[value] : "无");
const quote = (text) => `「${text}」`;

const FIELD_RENDERERS = {
  title: ([, next]) => `标题改为${quote(next)}`,
  description: () => "修改了描述",
  due_at: ([old, next]) => `截止时间 ${dueText(old)} → ${dueText(next)}`,
  priority: ([old, next]) => `优先级 ${priorityText(old)} → ${priorityText(next)}`,
  tags: ([old, next]) => `标签 ${old.join("、") || "无"} → ${next.join("、") || "无"}`,
};

/** Turns one log entry into a Chinese sentence (without the actor). */
export function describeEvent(event) {
  const { action, detail } = event;
  switch (action) {
    case "create": return `创建了任务${quote(detail.title)}`;
    case "status": return `将状态由 ${STATUS_LABEL[detail.status[0]]} 改为 ${STATUS_LABEL[detail.status[1]]}`;
    case "assign": {
      const parts = [];
      if (detail.added?.length) parts.push(`指派给 ${detail.added.join("、")}`);
      if (detail.removed?.length) parts.push(`取消了 ${detail.removed.join("、")} 的指派`);
      return parts.join("，") || "调整了负责人";
    }
    case "archive": return detail.reason === "auto" ? "任务完成满期，已自动归档" : "归档了任务";
    case "delete": return `删除了任务${quote(detail.snapshot?.title ?? "")}（已留存快照）`;
    default:
      return Object.entries(detail).map(([field, change]) => FIELD_RENDERERS[field]?.(change) ?? `修改了 ${field}`).join("；");
  }
}

export default {
  name: "EventList",
  props: {
    events: { type: Array, required: true },
    now: { type: Number, required: true },
    showTask: { type: Boolean, default: false },
    emptyText: { type: String, default: "还没有操作记录" },
  },
  setup() {
    const actorName = (event) => event.actor?.nickname || event.actor_label || "系统";
    return { describeEvent, actorName, formatDateTime, formatRelative, describeSession };
  },
  template: `
    <ol v-if="events.length" class="events">
      <li v-for="event in events" :key="event.id" class="event">
        <p class="event-line">
          <strong>{{ actorName(event) }}</strong>
          <span v-if="showTask" class="event-seq num">#{{ event.task_seq }}</span>
          {{ describeEvent(event) }}
        </p>
        <p class="event-meta num">
          <time :datetime="new Date(event.created_at * 1000).toISOString()" :title="formatDateTime(event.created_at)">{{ formatRelative(event.created_at, now) }}</time>
          <template v-if="event.session"> · {{ describeSession(event.session).text }}</template>
          <template v-else-if="event.actor_label"> · WebUI</template>
        </p>
      </li>
    </ol>
    <p v-else class="empty-note">{{ emptyText }}</p>
  `,
};
