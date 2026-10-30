// Tiny round avatar: the first character of a name on a muted palette colour picked from the name.
import { avatarColor, initial } from "../format.js";

export default {
  name: "Avatar",
  props: { name: { type: String, default: "" } },
  setup() {
    return { avatarColor, initial };
  },
  template: `<span class="avatar" :class="'c-' + avatarColor(name)" aria-hidden="true">{{ initial(name) }}</span>`,
};
