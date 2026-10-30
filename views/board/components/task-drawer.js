// Task detail drawer: edit every field, delete, and read the task's operation log.
// One save model: edits stay local until "保存"; closing with unsaved edits asks first.
import { computed, nextTick, onMounted, reactive, watch } from "../vue.js";
import { formatDateTime } from "../format.js";
import { draftFromTask, fieldsFromDraft, isSameDraft } from "../draft.js";
import { allTags, ask, closeTask, members, removeTask, saveTask, state } from "../store.js";
import EventList from "./event-list.js";
import Icon from "./icon.js";
import TaskFields from "./task-fields.js";

export default {
  name: "TaskDrawer",
  components: { EventList, Icon, TaskFields },
  setup() {
    const draft = reactive(draftFromTask(state.drawer.task));
    const original = computed(() => draftFromTask(state.drawer.task));
    const dirty = computed(() => !isSameDraft({ ...draft }, original.value));
    const task = computed(() => state.drawer.task);
    let opener = document.activeElement;

    // Server data refreshed after a save (or the first events load) resets the form only when untouched.
    watch(original, (next, prev) => { if (isSameDraft({ ...draft }, prev)) Object.assign(draft, next); });

    onMounted(async () => {
      await nextTick();
      document.querySelector(".drawer [data-autofocus]")?.focus();
    });

    async function requestClose() {
      if (dirty.value && !(await ask({ title: "放弃未保存的修改？", message: "关闭后本次修改将丢失。", confirmLabel: "放弃修改", danger: true }))) return;
      closeTask();
      opener?.isConnected && opener.focus();
    }

    async function save() {
      if (!draft.title.trim()) return;
      await saveTask(task.value.seq, fieldsFromDraft(draft, members.value));
    }

    async function remove() {
      const confirmed = await ask({
        title: `删除任务 #${task.value.seq}？`,
        message: `「${task.value.title}」将被永久删除，操作日志里会保留一份快照。`,
        confirmLabel: "删除任务",
        danger: true,
      });
      if (confirmed) await removeTask(task.value.seq);
    }

    function onKeydown(event) {
      if (event.key === "Escape" && !state.confirm) { event.stopPropagation(); requestClose(); }
    }

    return { state, draft, dirty, task, members, allTags, requestClose, save, remove, onKeydown, formatDateTime };
  },
  template: `
    <div class="drawer-layer" @keydown="onKeydown">
      <div class="drawer-scrim" @click="requestClose"></div>
      <aside class="drawer" role="dialog" :aria-label="'任务 #' + task.seq + ' 详情'">
        <header class="drawer-head">
          <h2 class="drawer-title"><span class="num muted">#{{ task.seq }}</span> 任务详情</h2>
          <button type="button" class="btn btn-icon btn-text" aria-label="关闭详情" @click="requestClose"><Icon name="close" /></button>
        </header>
        <form class="drawer-body" id="drawer-form" @submit.prevent="save">
          <TaskFields :draft="draft" :members="members" :tag-suggestions="allTags" full />
          <p class="drawer-facts hint num">
            创建人 {{ task.creator.nickname }} · 创建于 {{ formatDateTime(task.created_at) }} · 更新于 {{ formatDateTime(task.updated_at) }}
          </p>
          <section class="drawer-log" aria-labelledby="drawer-log-h">
            <h3 id="drawer-log-h" class="section-title">操作日志</h3>
            <EventList :events="state.drawer.events" :now="state.now" />
          </section>
        </form>
        <footer class="drawer-foot">
          <button type="button" class="btn btn-danger" @click="remove">删除</button>
          <span class="drawer-foot-gap"></span>
          <span v-if="dirty" class="hint">有未保存的修改</span>
          <button type="button" class="btn" @click="requestClose">关闭</button>
          <button type="submit" form="drawer-form" class="btn btn-primary" :disabled="!dirty || !draft.title.trim()">保存</button>
        </footer>
      </aside>
    </div>
  `,
};
