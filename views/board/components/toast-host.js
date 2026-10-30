// Transient messages; one polite live region. Errors stay readable for the same few seconds.
import { dismissToast, state } from "../store.js";
import Icon from "./icon.js";

export default {
  name: "ToastHost",
  components: { Icon },
  setup() {
    return { state, dismissToast };
  },
  template: `
    <div class="toasts" role="status" aria-live="polite">
      <div v-for="toast in state.toasts" :key="toast.id" class="toast" :class="'is-' + toast.kind">
        <span>{{ toast.message }}</span>
        <button type="button" class="toast-close" aria-label="关闭提示" @click="dismissToast(toast.id)"><Icon name="close" /></button>
      </div>
    </div>
  `,
};
