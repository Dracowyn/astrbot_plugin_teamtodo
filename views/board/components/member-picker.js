// Assignee chooser: checkboxes for the space's members (only members can be assigned), each with a tiny avatar.
import { LIMITS, userKey } from "../format.js";
import Avatar from "./avatar.js";

export default {
  name: "MemberPicker",
  components: { Avatar },
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
      <legend class="visually-hidden">负责人</legend>
      <div class="check-row">
        <label v-for="member in members" :key="userKey(member)" class="check">
          <input type="checkbox" :checked="modelValue.includes(userKey(member))" @change="toggle(userKey(member), $event.target.checked)">
          <Avatar :name="member.nickname" />{{ member.nickname }}
        </label>
        <span v-if="!members.length" class="hint">空间还没有成员</span>
      </div>
    </fieldset>
  `,
};
