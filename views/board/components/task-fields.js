// The field set shared by the inline create form (compact) and the detail drawer (full).
// Edits a draft object in place (the parent owns it); see draft.js for its shape.
import { LIMITS, STATUSES, STATUS_LABEL } from "../format.js";
import MemberPicker from "./member-picker.js";
import RichText from "./rich-text.js";
import TagInput from "./tag-input.js";

let uid = 0;

export default {
  name: "TaskFields",
  components: { MemberPicker, RichText, TagInput },
  props: {
    draft: { type: Object, required: true },
    members: { type: Array, required: true },
    tagSuggestions: { type: Array, default: () => [] },
    full: { type: Boolean, default: false }, // adds title, status and description
  },
  setup() {
    const id = `tf${++uid}`;
    const statuses = [...STATUSES, { key: "archived", label: STATUS_LABEL.archived }];
    return { id, statuses, LIMITS };
  },
  template: `
    <div class="fields">
      <div v-if="full" class="field">
        <label :for="id + '-title'">标题</label>
        <input :id="id + '-title'" v-model="draft.title" class="input" type="text" :maxlength="LIMITS.title" required data-autofocus>
      </div>
      <div class="fields-row">
        <div v-if="full" class="field">
          <label :for="id + '-status'">状态</label>
          <select :id="id + '-status'" v-model="draft.status" class="select">
            <option v-for="s in statuses" :key="s.key" :value="s.key">{{ s.label }}</option>
          </select>
        </div>
        <div class="field">
          <label :for="id + '-priority'">优先级</label>
          <select :id="id + '-priority'" v-model="draft.priority" class="select">
            <option value="">无</option><option value="high">高</option><option value="medium">中</option><option value="low">低</option>
          </select>
        </div>
      </div>
      <MemberPicker v-model="draft.assigneeKeys" :members="members" />
      <fieldset class="field fieldset-plain">
        <legend class="label">截止时间</legend>
        <div class="fields-row">
          <input v-model="draft.dueDate" class="input" type="date" aria-label="截止日期">
          <input v-model="draft.dueTime" class="input" type="time" aria-label="截止时刻" :disabled="!draft.dueDate">
          <button v-if="draft.dueDate" type="button" class="btn btn-text btn-sm" @click="draft.dueDate = ''; draft.dueTime = ''">清除</button>
        </div>
        <p class="hint">只选日期时默认当天 23:59 截止。</p>
      </fieldset>
      <div class="field">
        <span class="label">标签</span>
        <TagInput v-model="draft.tags" :suggestions="tagSuggestions" />
      </div>
      <div v-if="full" class="field">
        <label :for="id + '-desc'">描述</label>
        <textarea :id="id + '-desc'" v-model="draft.description" class="textarea" :maxlength="LIMITS.description"></textarea>
        <p class="hint num">{{ [...draft.description].length }} / {{ LIMITS.description }}，按纯文本显示，链接会自动识别</p>
        <details v-if="draft.description" class="preview">
          <summary>预览显示效果</summary>
          <RichText :text="draft.description" />
        </details>
      </div>
    </div>
  `,
};
