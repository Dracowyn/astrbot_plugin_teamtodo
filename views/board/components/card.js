// One task entry: number in a left gutter, title, then a single meta line (names · due · tags).
// Shared by the admin board (editable: click/Enter opens the drawer, draggable) and the public board
// (read-only: the description can be expanded in place). Overdue has exactly one signal: the due text.
import { computed, ref } from "../vue.js";
import { STATUS_LABEL, formatDue, isOverdue, userKey } from "../format.js";
import RichText from "./rich-text.js";

export default {
  name: "TaskCard",
  components: { RichText },
  props: {
    task: { type: Object, required: true },
    now: { type: Number, required: true },
    editable: { type: Boolean, default: false },
  },
  emits: ["open", "move-key"],
  setup(props, { emit }) {
    const expanded = ref(false);
    const overdue = computed(() => isOverdue(props.task, props.now));
    const dueText = computed(() => (props.task.due_at == null ? "" : formatDue(props.task.due_at, props.now)));
    const names = computed(() => props.task.assignees.map((a) => a.nickname).join("、"));
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

    return { expanded, overdue, dueText, names, label, onKeydown, userKey };
  },
  template: `
    <li class="card" :class="{ 'is-editable': editable, 'is-done': task.status === 'done', 'is-archived': task.status === 'archived' }"
        :data-seq="task.seq" :tabindex="editable ? 0 : undefined" :aria-label="editable ? label : undefined"
        :aria-keyshortcuts="editable ? 'Alt+ArrowLeft Alt+ArrowRight' : undefined"
        @click="editable && $emit('open', task.seq)" @keydown="editable && onKeydown($event)">
      <span class="card-seq num">#{{ task.seq }}</span>
      <div class="card-main">
        <div class="card-line">
          <p class="card-title">{{ task.title }}</p>
          <span v-if="task.priority === 'high'" class="prio" aria-hidden="true">▲</span>
          <span v-if="task.priority === 'high'" class="visually-hidden">高优先级</span>
        </div>
        <p v-if="names || task.due_at != null || task.tags.length" class="card-meta">
          <span v-if="names" class="card-names"><span class="visually-hidden">负责人 </span>{{ names }}</span>
          <span v-if="task.due_at != null" class="num" :class="{ 'is-overdue': overdue }"><span class="visually-hidden">截止 </span>{{ dueText }}<template v-if="overdue"> 逾期</template></span>
          <span v-if="task.tags.length" class="card-tags"><span v-for="tag in task.tags" :key="tag">#{{ tag }}</span></span>
        </p>
        <template v-if="!editable && task.description">
          <RichText :text="task.description" class="card-desc" :class="{ 'is-clamped': !expanded }" />
          <button type="button" class="card-toggle" :aria-expanded="expanded" @click="expanded = !expanded">
            {{ expanded ? '收起描述' : '展开描述' }}
          </button>
        </template>
      </div>
    </li>
  `,
};
