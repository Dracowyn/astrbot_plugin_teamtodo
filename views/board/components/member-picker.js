// Assignee chooser: plain checkboxes for the space's members (only members can be assigned).
import { LIMITS, userKey } from "../format.js";

export default {
  name: "MemberPicker",
  props: {
    members: { type: Array, required: true },
    modelValue: { type: Array, required: true }, // array of identity keys
  },
  emits: ["update:modelValue"],
  setup(props, { emit }) {
    function toggle(key, checked) {
      if (checked && props.modelValue.length >= LIMITS.assignees) return;
      emit("update:modelValue", checked ? [...props.modelValue, key] : props.modelValue.filter((k) => k !== key));
    }
    return { toggle, userKey };
  },
  template: `
    <fieldset class="member-picker">
      <legend class="label">负责人</legend>
      <div class="check-row">
        <label v-for="member in members" :key="userKey(member)" class="check">
          <input type="checkbox" :checked="modelValue.includes(userKey(member))" @change="toggle(userKey(member), $event.target.checked)">
          {{ member.nickname }}
        </label>
        <span v-if="!members.length" class="hint">空间还没有成员</span>
      </div>
    </fieldset>
  `,
};
