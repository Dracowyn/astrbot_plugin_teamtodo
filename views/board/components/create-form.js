// Create panel above the board, opened by the toolbar "新建" button or a column's "+" (which picks the status).
// A single title line (Enter creates) plus the optional property rows.
import { nextTick, reactive, ref, watch } from "../vue.js";
import { LIMITS, STATUS_COLOR, STATUS_LABEL } from "../format.js";
import { emptyDraft, fieldsFromDraft } from "../draft.js";
import { allTags, createTask, members, state, stopCreate } from "../store.js";
import InlineError from "./inline-error.js";
import Pill from "./pill.js";
import TaskFields from "./task-fields.js";

export default {
  name: "CreateForm",
  components: { InlineError, Pill, TaskFields },
  setup() {
    const draft = reactive(emptyDraft());
    const more = ref(false);
    const busy = ref(false);
    const titleInput = ref(null);

    watch(() => state.creating, async (status) => {
      if (!status) return;
      await nextTick();
      titleInput.value?.focus();
    }, { immediate: true });

    async function submit() {
      if (!draft.title.trim() || busy.value) return;
      busy.value = true;
      const { status, description, ...fields } = fieldsFromDraft(draft, members.value);
      const created = await createTask(fields, state.creating);
      busy.value = false;
      if (created) {
        Object.assign(draft, emptyDraft());
        titleInput.value?.focus();
      }
    }

    const onKeydown = (event) => { if (event.key === "Escape") stopCreate(); };

    return { state, draft, more, busy, titleInput, submit, stopCreate, onKeydown, members, allTags, LIMITS, STATUS_COLOR, STATUS_LABEL };
  },
  template: `
    <form class="create" @submit.prevent="submit" @keydown="onKeydown" aria-label="新建任务">
      <div class="create-row">
        <Pill :color="STATUS_COLOR[state.creating]" dot>{{ STATUS_LABEL[state.creating] }}</Pill>
        <label class="visually-hidden" for="create-title">新任务标题</label>
        <input id="create-title" ref="titleInput" v-model="draft.title" class="create-title" type="text" :maxlength="LIMITS.title"
               placeholder="任务标题，回车添加" autocomplete="off">
        <button type="button" class="btn btn-text" :aria-expanded="more" aria-controls="create-more" @click="more = !more">
          {{ more ? '收起属性' : '更多属性' }}
        </button>
        <button type="submit" class="btn btn-primary" :disabled="!draft.title.trim() || busy">{{ busy ? '添加中…' : '添加' }}</button>
        <button type="button" class="btn btn-text" @click="stopCreate">关闭</button>
      </div>
      <div v-if="more" id="create-more" class="create-more">
        <TaskFields :draft="draft" :members="members" :tag-suggestions="allTags" />
      </div>
      <InlineError scope="create" />
    </form>
  `,
};
