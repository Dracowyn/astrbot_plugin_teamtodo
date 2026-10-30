// The one icon still in use: a close cross on dismissible surfaces. No decorative icons.
import { h } from "../vue.js";

const PATHS = { close: "M4 4l8 8M12 4l-8 8" };

export default {
  name: "Icon",
  props: { name: { type: String, required: true } },
  setup(props) {
    return () =>
      h("svg", { class: "icon", viewBox: "0 0 16 16", "aria-hidden": "true", focusable: "false" }, [
        h("path", { d: PATHS[props.name] || "" }),
      ]);
  },
};
