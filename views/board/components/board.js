// The kanban board: groups tasks by status and renders one column each.
// `editable` switches between the admin board (drag, open details) and the read-only public board.
import { computed } from "../vue.js";
import { STATUSES, STATUS_LABEL, sortTasks } from "../format.js";
import BoardColumn from "./column.js";

const ARCHIVED_COLUMN = { key: "archived", label: STATUS_LABEL.archived };

export default {
  name: "KanbanBoard",
  components: { BoardColumn },
  props: {
    tasks: { type: Array, required: true },
    now: { type: Number, required: true },
    editable: { type: Boolean, default: false },
    showArchived: { type: Boolean, default: false },
  },
  emits: ["open", "move", "add"],
  setup(props, { emit }) {
    const columns = computed(() => {
      const list = props.showArchived ? [...STATUSES, ARCHIVED_COLUMN] : STATUSES;
      return list.map((column) => ({ ...column, tasks: sortTasks(props.tasks.filter((t) => t.status === column.key), column.key) }));
    });
    const order = computed(() => columns.value.map((column) => column.key));

    /** A drop reports the target status; an Alt+Arrow keypress reports a step along the column order. */
    function onMove({ seq, status, step }) {
      const task = props.tasks.find((t) => t.seq === seq);
      if (!task) return;
      const target = status ?? order.value[order.value.indexOf(task.status) + step];
      if (target && target !== task.status) emit("move", { seq, status: target });
    }

    return { columns, onMove };
  },
  template: `
    <div class="board" :style="{ '--cols': columns.length }" role="group" aria-label="任务看板">
      <BoardColumn v-for="column in columns" :key="column.key" :status="column.key" :label="column.label"
                   :tasks="column.tasks" :now="now" :editable="editable" :addable="editable && column.key !== 'archived'"
                   @open="$emit('open', $event)" @move="onMove" @add="$emit('add', $event)" />
    </div>
  `,
};
