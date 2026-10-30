// One task card: number and priority on top, the title, neutral tag pills, then a footer with the relative due chip and overlapping avatars.
// Shared by the admin board (editable: click/Enter opens the side peek, draggable) and the public board
// (read-only: the description can be expanded in place). Overdue has exactly one signal: red due text
// (the server's `overdue` flag; computed client-side only when a payload lacks it).
import { computed, ref } from "../vue.js";
import { PRIORITY_LABEL, STATUS_LABEL, describeDue, isOverdue } from "../format.js";
import Avatar from "./avatar.js";
import Icon from "./icon.js";
import Pill from "./pill.js";
import RichText from "./rich-text.js";

export default {
  name: "TaskCard",
  components: { Avatar, Icon, Pill, RichText },
  props: {
    task: { type: Object, required: true },
    now: { type: Number, required: true },
    editable: { type: Boolean, default: false },
  },
  emits: ["open", "move-key"],
  setup(props, { emit }) {
    const expanded = ref(false);
    const overdue = computed(() => (typeof props.task.overdue === "boolean" ? props.task.overdue : isOverdue(props.task, props.now)));
    const due = computed(() => (props.task.due_at == null ? null : describeDue(props.task.due_at, props.now, {
      open: props.task.status === "todo" || props.task.status === "doing", overdue: overdue.value,
    })));
    const label = computed(() => `#${props.task.seq} ${props.task.title}，${STATUS_LABEL[props.task.status]}`);

    function onKeydown(event) {
      if (event.target !== event.currentTarget) return;
      if (event.key === "Enter" || event.key === " ") {
        event.preventDefault();
        emit("open", props.task.seq);
      } else if (event.altKey && (event.key === "ArrowLeft" || event.key === "ArrowRight")) {
        event.preventDefault();
        emit("move-key", { seq: props.task.seq, step: event.key === "ArrowLeft" ? -1 : 1 });
      }
    }

    return { expanded, due, label, onKeydown, PRIORITY_LABEL };
  },
  template: `
    <li class="card" :class="{ 'is-editable': editable, 'is-done': task.status === 'done', 'is-archived': task.status === 'archived' }"
        :data-seq="task.seq" :tabindex="editable ? 0 : undefined" :aria-label="editable ? label : undefined"
        :aria-keyshortcuts="editable ? 'Alt+ArrowLeft Alt+ArrowRight' : undefined"
        @click="editable && $emit('open', task.seq)" @keydown="editable && onKeydown($event)">
      <div class="card-top">
        <span class="card-seq num">#{{ task.seq }}</span>
        <span v-if="task.priority" class="prio" :class="'is-' + task.priority">{{ PRIORITY_LABEL[task.priority] }}优先级</span>
      </div>
      <p class="card-title">{{ task.title }}</p>
      <div v-if="task.tags.length" class="card-tags">
        <Pill v-for="tag in task.tags" :key="tag" color="gray">{{ tag }}</Pill>
      </div>
      <div v-if="due || task.assignees.length" class="card-foot">
        <span v-if="due" class="due-chip num" :class="due.tone && 'is-' + due.tone"><Icon name="calendar" /><span class="visually-hidden">截止 </span>{{ due.text }}</span>
        <span v-if="task.assignees.length" class="card-people">
          <span class="visually-hidden">负责人 {{ task.assignees.map((a) => a.nickname).join('、') }}</span>
          <Avatar v-for="(a, i) in task.assignees.slice(0, 4)" :key="i + (a.user_id || a.nickname)" :name="a.nickname" />
          <span v-if="task.assignees.length > 4" class="card-more num" aria-hidden="true">+{{ task.assignees.length - 4 }}</span>
          <span v-if="task.assignees.length === 1" class="card-name" aria-hidden="true">{{ task.assignees[0].nickname }}</span>
        </span>
      </div>
      <template v-if="!editable && task.description">
        <RichText :text="task.description" class="card-desc" :class="{ 'is-clamped': !expanded }" />
        <button type="button" class="card-toggle" :aria-expanded="expanded" @click="expanded = !expanded">
          {{ expanded ? '收起描述' : '展开描述' }}
        </button>
      </template>
    </li>
  `,
};
