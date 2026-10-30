// Task detail as a Notion-style side peek: big title, property rows, description, then the activity history.
// One save model: edits stay local until "保存"; closing with unsaved edits asks first.
import { computed, nextTick, onMounted, reactive, watch } from "../vue.js";
import { formatDateTime } from "../format.js";
import { draftFromTask, isSameDraft, patchFromDraft } from "../draft.js";
import { allTags, ask, closeTask, members, removeTask, saveTask, state } from "../store.js";
import EventList from "./event-list.js";
import Icon from "./icon.js";
import InlineError from "./inline-error.js";
import TaskFields from "./task-fields.js";

export default {
  name: "TaskDrawer",
  components: { EventList, Icon, InlineError, TaskFields },
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
      document.querySelector(".peek [data-autofocus]")?.focus();
    });

    async function requestClose() {
      if (dirty.value && !(await ask({ title: "放弃未保存的修改？", message: "关闭后本次修改将丢失。", confirmLabel: "放弃修改", danger: true }))) return;
      closeTask();
      opener?.isConnected && opener.focus();
    }

    async function save() {
      if (!draft.title.trim()) return;
      const patch = patchFromDraft({ ...draft }, original.value, members.value);
      if (Object.keys(patch).length) await saveTask(task.value.seq, patch);
    }

    async function remove() {
      const confirmed = await ask({
        title: `删除任务 #${task.value.seq}？`,
        message: `「${task.value.title}」将被永久删除，操作日志里会保留一条删除记录。`,
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
    <div class="peek-layer" @keydown="onKeydown">
      <div class="peek-scrim" @click="requestClose"></div>
      <aside class="peek" role="dialog" :aria-label="'任务 #' + task.seq + ' 详情'">
        <header class="peek-bar">
          <span class="peek-crumb num">任务 #{{ task.seq }}</span>
          <button type="button" class="btn btn-icon btn-text" aria-label="关闭详情" @click="requestClose"><Icon name="close" /></button>
        </header>
        <form class="peek-body" id="drawer-form" @submit.prevent="save">
          <TaskFields :draft="draft" :members="members" :tag-suggestions="allTags" full>
            <div class="prop">
              <span class="prop-label"><Icon name="user" />创建人</span>
              <div class="prop-value prop-static">{{ task.creator.nickname }}</div>
            </div>
            <div class="prop">
              <span class="prop-label"><Icon name="clock" />创建 / 更新</span>
              <div class="prop-value prop-static num">{{ formatDateTime(task.created_at) }} / {{ formatDateTime(task.updated_at) }}</div>
            </div>
          </TaskFields>
          <section class="peek-log" aria-labelledby="drawer-log-h">
            <h3 id="drawer-log-h" class="section-title">活动</h3>
            <p v-if="state.drawer.loadingEvents" class="muted" role="status">正在加载日志…</p>
            <InlineError scope="drawerLog" />
            <EventList v-if="!state.drawer.loadingEvents && !state.errors.drawerLog" :events="state.drawer.events" :now="state.now" />
          </section>
        </form>
        <footer class="peek-foot">
          <button type="button" class="btn btn-danger" :disabled="state.pending.drawer" @click="remove"><Icon name="trash" />删除</button>
          <span class="peek-foot-gap"></span>
          <InlineError scope="drawer" />
          <span v-if="dirty && !state.errors.drawer" class="hint">有未保存的修改</span>
          <button type="button" class="btn" @click="requestClose">关闭</button>
          <button type="submit" form="drawer-form" class="btn btn-primary" :disabled="!dirty || !draft.title.trim() || state.pending.drawer">{{ state.pending.drawer ? '保存中…' : '保存' }}</button>
        </footer>
      </aside>
    </div>
  `,
};
