// Plain-text description renderer: keeps line breaks (CSS white-space: pre-wrap), turns only http(s)
// URLs into links. Built from Vue text nodes and <a> elements; data is never interpreted as HTML.
import { h } from "../vue.js";
import { splitLinks } from "../format.js";

export default {
  name: "RichText",
  props: { text: { type: String, default: "" } },
  setup(props) {
    return () =>
      h(
        "div",
        { class: "rich-text" },
        splitLinks(props.text).map((token) =>
          token.type === "link"
            ? h("a", { href: token.value, target: "_blank", rel: "noopener noreferrer" }, token.value)
            : token.value,
        ),
      );
  },
};
