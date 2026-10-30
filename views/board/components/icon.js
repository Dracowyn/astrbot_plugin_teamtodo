// Inline line icons: 16px grid, stroke 1.5, currentColor. One <path> per icon (sub-paths joined).
import { h } from "../vue.js";

const PATHS = {
  close: "M4 4l8 8M12 4l-8 8",
  plus: "M8 3v10M3 8h10",
  menu: "M2.5 4.5h11M2.5 8h11M2.5 11.5h11",
  status: "M8 2.5a5.5 5.5 0 1 0 0 11a5.5 5.5 0 0 0 0-11zM8 6a2 2 0 1 0 0 4a2 2 0 0 0 0-4z",
  calendar: "M3.5 4h9a1 1 0 0 1 1 1v7.5a1 1 0 0 1-1 1h-9a1 1 0 0 1-1-1V5a1 1 0 0 1 1-1zM2.5 7.5h11M5.5 2.5v3M10.5 2.5v3",
  user: "M8 8a2.5 2.5 0 1 0 0-5a2.5 2.5 0 0 0 0 5zM3 13.5c.5-2.5 2.5-3.8 5-3.8s4.5 1.3 5 3.8",
  tag: "M6.5 2.5l-1 11M10.5 2.5l-1 11M3 6h10.5M2.5 10h10.5",
  flag: "M4 14V2.5M4 3h7.5L10 5.5 11.5 8H4",
  text: "M3 4h10M3 8h10M3 12h6",
  clock: "M8 2.5a5.5 5.5 0 1 0 0 11a5.5 5.5 0 0 0 0-11zM8 5v3.2l2 1.3",
  more: "M3.5 8h.01M8 8h.01M12.5 8h.01",
  link: "M6.5 9.5a2.5 2.5 0 0 0 3.5 0l2-2a2.5 2.5 0 0 0-3.5-3.5l-.5.5M9.5 6.5a2.5 2.5 0 0 0-3.5 0l-2 2a2.5 2.5 0 0 0 3.5 3.5l.5-.5",
  trash: "M3 4.5h10M6.5 4.5V3h3v1.5M4.5 4.5l.6 8.5h5.8l.6-8.5M7 7v4M9 7v4",
  board: "M2.5 3h3v10h-3zM6.5 3h3v6h-3zM10.5 3h3v8h-3z",
  log: "M3 4h.01M3 8h.01M3 12h.01M6 4h7M6 8h7M6 12h7",
  settings: "M3 5h6M11.5 5H13M3 11h1.5M7 11h6M9 3.5v3M5 9.5v3",
};

export default {
  name: "Icon",
  props: { name: { type: String, required: true } },
  setup(props) {
    return () =>
      h("svg", { class: "icon", viewBox: "0 0 16 16", "aria-hidden": "true", focusable: "false" }, [h("path", { d: PATHS[props.name] || "" })]);
  },
};
