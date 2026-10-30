// Task properties as Notion-style rows (line icon + gray label + value). Shared by the create panel (compact)
// and the side peek (full: big title, status, extra read-only rows via the default slot, then the description).
// Edits a draft object in place (the parent owns it); see draft.js for its shape.
import { LIMITS, PRIORITY_COLOR, PRIORITY_LABEL, STATUS_COLOR, STATUSES, STATUS_LABEL } from "../format.js";
import Icon from "./icon.js";
import MemberPicker from "./member-picker.js";
import PillSelect from "./pill-select.js";
import RichText from "./rich-text.js";
import TagInput from "./tag-input.js";

let uid = 0;

const STATUS_OPTIONS = [...STATUSES, { key: "archived", label: STATUS_LABEL.archived }].map((s) => ({ value: s.key, label: s.label, color: STATUS_COLOR[s.key] }));
const PRIORITY_OPTIONS = [
  { value: "", label: "未设置", color: "none" },
  ...["high", "medium", "low"].map((key) => ({ value: key, label: PRIORITY_LABEL[key], color: PRIORITY_COLOR[key] })),
];

export default {
  name: "TaskFields",
  components: { Icon, MemberPicker, PillSelect, RichText, TagInput },
  props: {
    draft: { type: Object, required: true },
    members: { type: Array, required: true },
    tagSuggestions: { type: Array, default: () => [] },
    full: { type: Boolean, default: false }, // adds title, status and description
  },
  setup() {
    return { id: `tf${++uid}`, LIMITS, STATUS_OPTIONS, PRIORITY_OPTIONS };
  },
  template: `
    <div class="fields">
      <input v-if="full" :id="id + '-title'" v-model="draft.title" class="peek-title" type="text" :maxlength="LIMITS.title"
             placeholder="无标题" aria-label="标题" required data-autofocus>
      <div class="props">
        <div v-if="full" class="prop">
          <span class="prop-label"><Icon name="status" />状态</span>
          <div class="prop-value"><PillSelect v-model="draft.status" :options="STATUS_OPTIONS" label="状态" dot /></div>
        </div>
        <div class="prop">
          <span class="prop-label"><Icon name="flag" />优先级</span>
          <div class="prop-value"><PillSelect v-model="draft.priority" :options="PRIORITY_OPTIONS" label="优先级" /></div>
        </div>
        <div class="prop">
          <span class="prop-label"><Icon name="user" />负责人</span>
          <div class="prop-value"><MemberPicker v-model="draft.assigneeKeys" :members="members" /></div>
        </div>
        <div class="prop">
          <span class="prop-label"><Icon name="calendar" />截止时间</span>
          <div class="prop-value">
            <div class="due-row">
              <input v-model="draft.dueDate" class="input input-flat" type="date" aria-label="截止日期">
              <input v-model="draft.dueTime" class="input input-flat" type="time" aria-label="截止时刻" :disabled="!draft.dueDate">
              <button v-if="draft.dueDate" type="button" class="btn btn-text btn-sm" @click="draft.dueDate = ''; draft.dueTime = ''">清除</button>
            </div>
            <p v-if="draft.dueDate" class="hint">未选时刻时默认当天 23:59 截止。</p>
          </div>
        </div>
        <div class="prop">
          <span class="prop-label"><Icon name="tag" />标签</span>
          <div class="prop-value"><TagInput v-model="draft.tags" :suggestions="tagSuggestions" /></div>
        </div>
        <slot />
      </div>
      <template v-if="full">
        <hr class="divider">
        <div class="desc">
          <label class="visually-hidden" :for="id + '-desc'">描述</label>
          <textarea :id="id + '-desc'" v-model="draft.description" class="textarea textarea-flat" :maxlength="LIMITS.description"
                    placeholder="添加描述…（纯文本，链接会自动识别）"></textarea>
          <p class="hint num">{{ [...draft.description].length }} / {{ LIMITS.description }}</p>
          <details v-if="draft.description" class="preview">
            <summary>预览显示效果</summary>
            <RichText :text="draft.description" />
          </details>
        </div>
      </template>
    </div>
  `,
};
