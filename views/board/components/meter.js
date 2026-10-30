// Thin decorative progress bar (value 0..1). The meaning is always stated in text next to it.
import { computed } from "../vue.js";

export default {
  name: "Meter",
  props: { value: { type: Number, default: 0 } },
  setup(props) {
    const width = computed(() => `${Math.round(Math.min(1, Math.max(0, props.value)) * 100)}%`);
    return { width };
  },
  template: `<span class="meter" aria-hidden="true"><i :style="{ width }"></i></span>`,
};
