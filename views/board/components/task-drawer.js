// Task detail as a centred page modal with a cover strip echoing its space: title, property rows, description, activity.
// One save model: edits stay local until "保存"; closing with unsaved edits asks first. While open it traps Tab,
// locks page scroll, makes the page behind inert and returns focus to the opener on close.
import { computed, nextTick, onBeforeUnmount, onMounted, reactive, watch } from "../vue.js";
import { formatDateTime, toneClass } from "../format.js";
import { draftFromTask, isSameDraft, patchFromDraft } from "../draft.js";
import { allTags, ask, closeTask, members, removeTask, saveTask, state } from "../store.js";
import EventList from "./event-list.js";
import Icon from "./icon.js";
import InlineError from "./inline-error.js";
import SpaceIcon from "./space-icon.js";
import TaskFields from "./task-fields.js";

export default {
  name: "TaskDrawer",
  components: { EventList, Icon, InlineError, SpaceIcon, TaskFields },
  setup() {
    const draft = reactive(draftFromTask(state.drawer.task));
    const original = computed(() => draftFromTask(state.drawer.task));
    const dirty = computed(() => !isSameDraft({ ...draft }, original.value));
    const task = computed(() => state.drawer.task);
    const opener = document.activeElement;
    const background = [...document.querySelectorAll(".sidebar, .main")];
    const FOCUSABLE = 'a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])';

    // Server data refreshed after a save (or the first events load) resets the form only when untouched.
    watch(original, (next, prev) => { if (isSameDraft({ ...draft }, prev)) Object.assign(draft, next); });

    onMounted(async () => {
      document.documentElement.classList.add("is-modal-open");
      document.addEventListener("keydown", onKeydown);
      background.forEach((el) => { el.inert = true; });
      await nextTick();
      document.querySelector(".modal [data-autofocus]")?.focus();
    });
    onBeforeUnmount(() => {
      document.removeEventListener("keydown", onKeydown);
      document.documentElement.classList.remove("is-modal-open");
      background.forEach((el) => { el.inert = false; });
      // The opener was inert until now, so focus returns on the next tick (a deleted card is simply gone).
      nextTick(() => opener?.isConnected && opener.focus());
    });

    async function requestClose() {
      if (dirty.value && !(await ask({ title: "放弃未保存的修改？", message: "关闭后本次修改将丢失。", confirmLabel: "放弃修改", danger: true }))) return;
      closeTask();
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

    /** Document-level so Escape works even when focus fell to the body (e.g. a button disabled while saving). The people
     *  popover swallows its own Escape before it bubbles here; Tab wraps inside the dialog. */
    function onKeydown(event) {
      if (event.key === "Escape" && !state.confirm) {
        event.preventDefault(); // else the browser's close request would cancel the confirm dialog opened below
        event.stopPropagation();
        requestClose();
        return;
      }
      if (event.key !== "Tab") return;
      const items = [...document.querySelector(".modal").querySelectorAll(FOCUSABLE)].filter((el) => el.offsetParent !== null || el === document.activeElement);
      if (!items.length) return;
      const first = items[0];
      const last = items[items.length - 1];
      if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus(); }
      else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
    }

    return { state, draft, dirty, task, members, allTags, requestClose, save, remove, onKeydown, formatDateTime, toneClass };
  },
  template: `
    <div class="modal-layer">
      <div class="modal-scrim" @click="requestClose"></div>
      <section class="modal" :class="toneClass(state.space.name)" role="dialog" aria-modal="true" :aria-label="'任务 #' + task.seq + ' 详情'">
        <header class="cover modal-cover">
          <span class="modal-crumb"><SpaceIcon :name="state.space.name" /><span>{{ state.space.name }}</span><span class="num">· 任务 #{{ task.seq }}</span></span>
          <button type="button" class="btn btn-icon btn-text" aria-label="关闭详情" @click="requestClose"><Icon name="close" /></button>
        </header>
        <form class="modal-body" id="drawer-form" @submit.prevent="save">
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
          <section class="modal-log" aria-labelledby="drawer-log-h">
            <h3 id="drawer-log-h" class="section-title">活动</h3>
            <p v-if="state.drawer.loadingEvents" class="muted" role="status">正在加载日志…</p>
            <InlineError scope="drawerLog" />
            <EventList v-if="!state.drawer.loadingEvents && !state.errors.drawerLog" :events="state.drawer.events" :now="state.now" />
          </section>
        </form>
        <footer class="modal-foot">
          <button type="button" class="btn btn-danger" :disabled="state.pending.drawer" @click="remove"><Icon name="trash" />删除</button>
          <span class="modal-foot-gap"></span>
          <InlineError scope="drawer" />
          <span v-if="dirty && !state.errors.drawer" class="hint">有未保存的修改</span>
          <button type="button" class="btn" @click="requestClose">关闭</button>
          <button type="submit" form="drawer-form" class="btn btn-primary" :disabled="!dirty || !draft.title.trim() || state.pending.drawer">{{ state.pending.drawer ? '保存中…' : '保存' }}</button>
        </footer>
      </section>
    </div>
  `,
};
