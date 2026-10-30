// Read-only value joined to a copy button (invite code, share link). Clipboard failures fall back to
// selecting the text so it can be copied by hand.
import { ref } from "../vue.js";
import { notify } from "../store.js";

let uid = 0;

export default {
  name: "CopyField",
  props: { label: { type: String, required: true }, value: { type: String, required: true } },
  setup(props) {
    const id = `copy-${++uid}`;
    const copied = ref(false);
    const input = ref(null);

    async function copy() {
      try {
        await navigator.clipboard.writeText(props.value);
        copied.value = true;
        setTimeout(() => { copied.value = false; }, 1800);
      } catch {
        input.value?.select();
        notify("无法访问剪贴板，已选中文本，请按 Ctrl+C 复制", "error");
      }
    }
    return { id, copied, input, copy };
  },
  template: `
    <div class="field">
      <label :for="id">{{ label }}</label>
      <div class="joined">
        <input :id="id" ref="input" class="input" type="text" readonly :value="value" @focus="$event.target.select()">
        <button type="button" class="btn" @click="copy">{{ copied ? '已复制' : '复制' }}</button>
      </div>
    </div>
  `,
};
