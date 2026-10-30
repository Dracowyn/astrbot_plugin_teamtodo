// One kanban column. When editable it becomes a SortableJS drop target; cards dragged in from another
// column are put back by the DOM handler and the move is reported upwards, so Vue stays the single
// owner of the DOM and the new state arrives through props.
import { nextTick, onBeforeUnmount, onMounted, ref } from "../vue.js";
import TaskCard from "./card.js";

const prefersReducedMotion = () => window.matchMedia("(prefers-reduced-motion: reduce)").matches;

export default {
  name: "BoardColumn",
  components: { TaskCard },
  props: {
    status: { type: String, required: true },
    label: { type: String, required: true },
    tasks: { type: Array, required: true },
    now: { type: Number, required: true },
    editable: { type: Boolean, default: false },
  },
  emits: ["open", "move"],
  setup(props, { emit }) {
    const list = ref(null);
    let sortable = null;

    onMounted(async () => {
      if (!props.editable) return;
      const { default: Sortable } = await import("../vendor/sortable.esm.js");
      if (!list.value) return;
      sortable = Sortable.create(list.value, {
        group: "board",
        sort: false,
        animation: prefersReducedMotion() ? 0 : 150,
        delay: 160,
        delayOnTouchOnly: true,
        ghostClass: "is-ghost",
        chosenClass: "is-chosen",
        dragClass: "is-dragging",
        onEnd(event) {
          if (event.from === event.to) return;
          event.to.removeChild(event.item);
          event.from.insertBefore(event.item, event.from.children[event.oldIndex] ?? null);
          emit("move", { seq: Number(event.item.dataset.seq), status: event.to.dataset.status });
        },
      });
    });
    onBeforeUnmount(() => sortable?.destroy());

    /** Alt+Arrow on a focused card: move to the neighbouring column and keep keyboard focus on it. */
    async function onMoveKey({ seq, step }) {
      emit("move", { seq, step });
      await nextTick();
      document.querySelector(`.card[data-seq="${seq}"]`)?.focus();
    }

    return { list, onMoveKey };
  },
  template: `
    <section class="col" :class="'col-' + status" :aria-labelledby="'col-h-' + status">
      <header class="col-head">
        <h2 :id="'col-h-' + status" class="col-title">{{ label }}</h2>
        <span class="col-rule" aria-hidden="true"></span>
        <span class="col-count num" :aria-label="tasks.length + ' 项'">{{ tasks.length }}</span>
      </header>
      <div class="col-body">
        <ul ref="list" class="cards" :data-status="status">
          <TaskCard v-for="task in tasks" :key="task.seq" :task="task" :now="now" :editable="editable"
                    @open="$emit('open', $event)" @move-key="onMoveKey" />
        </ul>
        <p v-if="!tasks.length" class="col-empty">{{ editable ? '没有任务，可拖到这里' : '没有任务' }}</p>
      </div>
    </section>
  `,
};
