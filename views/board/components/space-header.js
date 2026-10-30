// Title row of the selected space: name, two quiet lines of plain text, then the view tabs
// (看板 / 操作日志 / 设置).
import { computed } from "../vue.js";
import { isOverdue } from "../format.js";
import { setView, state } from "../store.js";

const TABS = [
  { key: "board", label: "看板" },
  { key: "log", label: "操作日志" },
  { key: "settings", label: "设置" },
];

export default {
  name: "SpaceHeader",
  setup() {
    const active = computed(() => state.tasks.filter((t) => t.status !== "archived"));
    const summary = computed(() => {
      const count = (status) => active.value.filter((t) => t.status === status).length;
      const overdue = active.value.filter((t) => isOverdue(t, state.now)).length;
      if (!active.value.length) return "还没有任务";
      return `共 ${active.value.length} 项，已完成 ${count("done")}${overdue ? `，逾期 ${overdue}` : ""}`;
    });
    const facts = computed(() => [
      state.space.bindings.length ? `绑定 ${state.space.bindings.length} 个会话` : "未绑定",
      `${state.space.members.length} 位成员`,
      state.space.owner ? `所有者 ${state.space.owner.nickname}` : "所有者待认领",
      ...(state.space.share.enabled ? ["公开链接已开启"] : []),
    ]);

    function onTabKeydown(event) {
      const index = TABS.findIndex((tab) => tab.key === state.view);
      const step = { ArrowRight: 1, ArrowLeft: -1 }[event.key];
      if (!step) return;
      event.preventDefault();
      const next = TABS[(index + step + TABS.length) % TABS.length];
      setView(next.key);
      event.currentTarget.querySelector(`#tab-${next.key}`)?.focus();
    }

    return { state, TABS, summary, facts, setView, onTabKeydown };
  },
  template: `
    <header class="space-head">
      <h1 class="space-title">{{ state.space.name }}</h1>
      <p class="space-sub num">
        <span v-for="fact in facts" :key="fact">{{ fact }}</span>
      </p>
      <p class="space-sub num">{{ summary }}</p>
      <div class="tabs" role="tablist" aria-label="空间视图" @keydown="onTabKeydown">
        <button v-for="tab in TABS" :key="tab.key" :id="'tab-' + tab.key" type="button" role="tab" class="tab"
                :aria-selected="state.view === tab.key" :tabindex="state.view === tab.key ? 0 : -1" aria-controls="view-panel"
                @click="setView(tab.key)">{{ tab.label }}</button>
      </div>
    </header>
  `,
};
