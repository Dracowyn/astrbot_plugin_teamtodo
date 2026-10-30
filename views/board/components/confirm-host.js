// Single native <dialog> driven by store.ask(). Native modal gives focus trapping and Esc for free.
// Dangerous confirmations can require typing the object's name.
import { computed, nextTick, ref, watch } from "../vue.js";
import { answer, state } from "../store.js";

export default {
  name: "ConfirmHost",
  setup() {
    const dialog = ref(null);
    const typed = ref("");
    const canConfirm = computed(() => !state.confirm?.requireText || typed.value.trim() === state.confirm.requireText);

    watch(() => state.confirm, async (pending) => {
      if (pending) {
        typed.value = "";
        await nextTick();
        if (!dialog.value.open) dialog.value.showModal();
      } else if (dialog.value?.open) {
        dialog.value.close();
      }
    });

    const onBackdrop = (event) => { if (event.target === dialog.value) answer(false); };
    return { state, dialog, typed, canConfirm, answer, onBackdrop };
  },
  template: `
    <dialog ref="dialog" class="dialog" aria-labelledby="confirm-title" @cancel.prevent="answer(false)" @click="onBackdrop">
      <form v-if="state.confirm" class="dialog-body" @submit.prevent="canConfirm && answer(true)">
        <h2 id="confirm-title" class="dialog-title">{{ state.confirm.title }}</h2>
        <p class="dialog-message">{{ state.confirm.message }}</p>
        <div v-if="state.confirm.requireText" class="field">
          <label for="confirm-typed">空间名称</label>
          <input id="confirm-typed" v-model="typed" class="input" type="text" autocomplete="off" autofocus>
        </div>
        <div class="dialog-actions">
          <button type="button" class="btn" :autofocus="!state.confirm.requireText" @click="answer(false)">取消</button>
          <button type="submit" class="btn" :class="state.confirm.danger ? 'btn-danger is-solid' : 'btn-primary'" :disabled="!canConfirm">{{ state.confirm.confirmLabel }}</button>
        </div>
      </form>
    </dialog>
  `,
};
