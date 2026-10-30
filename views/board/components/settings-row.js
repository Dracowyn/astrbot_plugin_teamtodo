// One settings section: a label column (title + description) beside its panel.
let uid = 0;

export default {
  name: "SettingsRow",
  props: { title: { type: String, required: true }, description: { type: String, default: "" } },
  setup() {
    return { id: `srow-${++uid}` };
  },
  template: `
    <section class="srow" :aria-labelledby="id">
      <div class="srow-label">
        <h3 :id="id" class="section-title">{{ title }}</h3>
        <p v-if="description" class="hint">{{ description }}</p>
      </div>
      <div class="srow-panel"><slot /></div>
    </section>
  `,
};
