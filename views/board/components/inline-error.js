// Failure message shown next to the action that failed (see store.js attempt()). Empty when there is none.
import { state } from "../store.js";

export default {
  name: "InlineError",
  props: { scope: { type: String, required: true } },
  setup() {
    return { state };
  },
  template: `<p v-if="state.errors[scope]" class="form-error" role="alert">{{ state.errors[scope] }}</p>`,
};
