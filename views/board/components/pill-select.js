// Select that looks like a Notion pill: a Pill shows the value, a transparent native <select> on top handles input.
import { computed } from "../vue.js";
import Pill from "./pill.js";

export default {
  name: "PillSelect",
  components: { Pill },
  props: {
    modelValue: { type: String, required: true },
    options: { type: Array, required: true }, // [{ value, label, color }]
    label: { type: String, required: true }, // accessible name
    dot: { type: Boolean, default: false },
  },
  emits: ["update:modelValue"],
  setup(props) {
    const current = computed(() => props.options.find((o) => o.value === props.modelValue) ?? props.options[0]);
    return { current };
  },
  template: `
    <span class="pill-select">
      <Pill :color="current.color" :dot="dot">{{ current.label }}</Pill>
      <select class="pill-select-input" :aria-label="label" :value="modelValue" @change="$emit('update:modelValue', $event.target.value)">
        <option v-for="o in options" :key="o.value" :value="o.value">{{ o.label }}</option>
      </select>
    </span>
  `,
};
