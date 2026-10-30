// One task card: title, then a row of small properties (number, priority, tags, due, people).
// Shared by the admin board (editable: click/Enter opens the side peek, draggable) and the public board
// (read-only: the description can be expanded in place). Overdue has exactly one signal: red due text
// (admin: the server's `overdue` flag; public board: computed client-side until M4).
import { computed, ref } from "../vue.js";
import { PRIORITY_COLOR, PRIORITY_LABEL, STATUS_LABEL, formatDue, isOverdue, tagColor } from "../format.js";
import Avatar from "./avatar.js";
import Pill from "./pill.js";
import RichText from "./rich-text.js";

export default {
  name: "TaskCard",
  components: { Avatar, Pill, RichText },
  props: {
    task: { type: Object, required: true },
    now: { type: Number, required: true },
    editable: { type: Boolean, default: false },
  },
  emits: ["open", "move-key"],
  setup(props, { emit }) {
    const expanded = ref(false);
    const overdue = computed(() => (props.editable ? props.task.overdue === true : isOverdue(props.task, props.now)));
    const dueText = computed(() => (props.task.due_at == null ? "" : formatDue(props.task.due_at, props.now)));
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

    return { expanded, overdue, dueText, label, onKeydown, PRIORITY_COLOR, PRIORITY_LABEL, tagColor };
  },
  template: `
    <li class="card" :class="{ 'is-editable': editable, 'is-done': task.status === 'done', 'is-archived': task.status === 'archived' }"
        :data-seq="task.seq" :tabindex="editable ? 0 : undefined" :aria-label="editable ? label : undefined"
        :aria-keyshortcuts="editable ? 'Alt+ArrowLeft Alt+ArrowRight' : undefined"
        @click="editable && $emit('open', task.seq)" @keydown="editable && onKeydown($event)">
      <p class="card-title">{{ task.title }}</p>
      <div class="card-props">
        <span class="card-seq num">#{{ task.seq }}</span>
        <Pill v-if="task.priority" :color="PRIORITY_COLOR[task.priority]"><span class="visually-hidden">优先级 </span>{{ PRIORITY_LABEL[task.priority] }}</Pill>
        <Pill v-for="tag in task.tags" :key="tag" :color="tagColor(tag)">{{ tag }}</Pill>
        <span v-if="task.due_at != null" class="card-due num" :class="{ 'is-overdue': overdue }"><span class="visually-hidden">截止 </span>{{ dueText }}<span v-if="overdue" class="visually-hidden"> 逾期</span></span>
        <span v-if="task.assignees.length" class="card-people">
          <span class="visually-hidden">负责人 {{ task.assignees.map((a) => a.nickname).join('、') }}</span>
          <Avatar v-for="a in task.assignees.slice(0, 4)" :key="a.platform_id + a.user_id" :name="a.nickname" />
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
