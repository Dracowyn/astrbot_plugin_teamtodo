// Inline create form above the board: a single title line (Enter creates), optional extra fields.
import { reactive, ref } from "../vue.js";
import { LIMITS } from "../format.js";
import { emptyDraft, fieldsFromDraft } from "../draft.js";
import { createTask, members, allTags } from "../store.js";
import TaskFields from "./task-fields.js";

export default {
  name: "CreateForm",
  components: { TaskFields },
  setup() {
    const draft = reactive(emptyDraft());
    const more = ref(false);
    const busy = ref(false);
    const titleInput = ref(null);

    async function submit() {
      if (!draft.title.trim() || busy.value) return;
      busy.value = true;
      const { status, description, ...fields } = fieldsFromDraft(draft, members.value);
      const created = await createTask(fields);
      busy.value = false;
      if (created) {
        Object.assign(draft, emptyDraft());
        titleInput.value?.focus();
      }
    }

    return { draft, more, busy, titleInput, submit, members, allTags, LIMITS };
  },
  template: `
    <form class="create" @submit.prevent="submit" aria-label="新建任务">
      <div class="create-row">
        <label class="visually-hidden" for="create-title">新任务标题</label>
        <input id="create-title" ref="titleInput" v-model="draft.title" class="input" type="text" :maxlength="LIMITS.title"
               placeholder="新任务标题，回车添加到待办" autocomplete="off">
        <button type="button" class="btn btn-text" :aria-expanded="more" aria-controls="create-more" @click="more = !more">
          更多选项
        </button>
        <button type="submit" class="btn btn-primary" :disabled="!draft.title.trim() || busy">添加</button>
      </div>
      <div v-if="more" id="create-more" class="create-more">
        <TaskFields :draft="draft" :members="members" :tag-suggestions="allTags" />
      </div>
    </form>
  `,
};
