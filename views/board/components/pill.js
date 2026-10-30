// Select / tag pill. `color` is one of the palette names (gray, blue, ...); `dot` makes it a round status pill with a dot.
export default {
  name: "Pill",
  props: { color: { type: String, default: "gray" }, dot: { type: Boolean, default: false } },
  template: `<span class="pill" :class="['c-' + color, { 'pill-status': dot }]"><span v-if="dot" class="pill-dot" aria-hidden="true"></span><span class="pill-label"><slot /></span></span>`,
};
