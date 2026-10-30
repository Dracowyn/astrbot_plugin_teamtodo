// Square-ish tile showing the first character of a space name; colours come from the nearest .tone-N ancestor, size from --icon-size.
import { initial } from "../format.js";

export default {
  name: "SpaceIcon",
  props: { name: { type: String, default: "" } },
  setup() {
    return { initial };
  },
  template: `<span class="space-icon" aria-hidden="true">{{ name ? initial(name) : '·' }}</span>`,
};
