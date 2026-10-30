// Tag editor: plain "#tag ×" entries and a text input (Enter or comma adds). Suggestions come from
// tags already used in the space. Limits mirror core/models.py.
import { ref } from "../vue.js";
import { LIMITS } from "../format.js";

let uid = 0;

export default {
  name: "TagInput",
  props: {
    modelValue: { type: Array, required: true },
    suggestions: { type: Array, default: () => [] },
  },
  emits: ["update:modelValue"],
  setup(props, { emit }) {
    const text = ref("");
    const error = ref("");
    const listId = `tag-suggestions-${++uid}`;

    function add() {
      const tag = text.value.trim().replace(/,$/, "").trim();
      if (!tag) return;
      if ([...tag].length > LIMITS.tag) { error.value = `单个标签最多 ${LIMITS.tag} 字`; return; }
      if (props.modelValue.includes(tag)) { text.value = ""; return; }
      if (props.modelValue.length >= LIMITS.tags) { error.value = `标签最多 ${LIMITS.tags} 个`; return; }
      error.value = "";
      emit("update:modelValue", [...props.modelValue, tag]);
      text.value = "";
    }
    function remove(tag) {
      emit("update:modelValue", props.modelValue.filter((t) => t !== tag));
    }
    function onKeydown(event) {
      if (event.key === "Enter" || event.key === "," || event.key === "，") {
        event.preventDefault();
        add();
      }
    }
    return { text, error, listId, add, remove, onKeydown, LIMITS };
  },
  template: `
    <div class="tag-input">
      <ul v-if="modelValue.length" class="tag-list" aria-label="已添加的标签">
        <li v-for="tag in modelValue" :key="tag">
          #{{ tag }}<button type="button" class="tag-remove" :aria-label="'移除标签 ' + tag" @click="remove(tag)">×</button>
        </li>
      </ul>
      <input v-model="text" class="input" type="text" :list="listId" :maxlength="LIMITS.tag * 2" placeholder="输入标签，回车添加"
             aria-label="添加标签" @keydown="onKeydown" @blur="add">
      <datalist :id="listId"><option v-for="tag in suggestions" :key="tag" :value="tag"></option></datalist>
      <p v-if="error" class="error-text" role="alert">{{ error }}</p>
    </div>
  `,
};
