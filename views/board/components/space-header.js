// Page header of the selected space, like a Notion page: icon tile, big title, a compact property strip,
// then the view tabs (看板 / 日志 / 设置) with the "新建" button on the right.
import { computed } from "../vue.js";
import { initial, tagColor } from "../format.js";
import { setView, startCreate, state } from "../store.js";
import Icon from "./icon.js";

const TABS = [
  { key: "board", label: "看板", icon: "board" },
  { key: "log", label: "日志", icon: "log" },
  { key: "settings", label: "设置", icon: "settings" },
];

export default {
  name: "SpaceHeader",
  components: { Icon },
  setup() {
    const active = computed(() => state.tasks.filter((t) => t.status !== "archived"));
    const overdue = computed(() => active.value.filter((t) => t.overdue).length);
    const facts = computed(() => [
      state.space.owner ? `所有者 ${state.space.owner.nickname}` : "所有者待认领",
      state.space.bindings.length ? `${state.space.bindings.length} 个会话` : "未绑定会话",
      `${state.space.members.length} 位成员`,
      active.value.length ? `${active.value.length} 项任务，已完成 ${active.value.filter((t) => t.status === "done").length}` : "还没有任务",
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

    return { state, TABS, facts, overdue, setView, startCreate, onTabKeydown, initial, tagColor };
  },
  template: `
    <header class="page-head">
      <span class="page-icon" :class="'c-' + tagColor(state.space.name)" aria-hidden="true">{{ initial(state.space.name) }}</span>
      <h1 class="page-title">{{ state.space.name }}</h1>
      <p class="props-strip">
        <span v-for="fact in facts" :key="fact">{{ fact }}</span>
        <span v-if="overdue" class="is-overdue num">逾期 {{ overdue }}</span>
      </p>
      <div class="tabs-row">
        <div class="tabs" role="tablist" aria-label="空间视图" @keydown="onTabKeydown">
          <button v-for="tab in TABS" :key="tab.key" :id="'tab-' + tab.key" type="button" role="tab" class="tab"
                  :aria-selected="state.view === tab.key" :tabindex="state.view === tab.key ? 0 : -1" aria-controls="view-panel"
                  @click="setView(tab.key)"><Icon :name="tab.icon" />{{ tab.label }}</button>
        </div>
        <div class="toolbar">
          <button v-if="state.view === 'board'" type="button" class="btn btn-primary" :aria-expanded="state.creating !== null"
                  @click="startCreate('todo')"><Icon name="plus" />新建</button>
        </div>
      </div>
    </header>
  `,
};
