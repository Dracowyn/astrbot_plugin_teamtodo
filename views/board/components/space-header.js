// Page header of the selected space: a tinted cover band, the overlapping icon tile, the title, a stats strip with a
// completion meter, then the view tabs (看板 / 日志 / 设置) with the "新建" button on the right.
import { computed } from "../vue.js";
import { toneClass } from "../format.js";
import { setView, startCreate, state } from "../store.js";
import Icon from "./icon.js";
import Meter from "./meter.js";
import SpaceIcon from "./space-icon.js";

const TABS = [
  { key: "board", label: "看板", icon: "board" },
  { key: "log", label: "日志", icon: "log" },
  { key: "settings", label: "设置", icon: "settings" },
];

export default {
  name: "SpaceHeader",
  components: { Icon, Meter, SpaceIcon },
  setup() {
    const active = computed(() => state.tasks.filter((t) => t.status !== "archived"));
    const overdue = computed(() => active.value.filter((t) => t.overdue).length);
    const done = computed(() => active.value.filter((t) => t.status === "done").length);
    const facts = computed(() => [
      state.space.owner ? `所有者 ${state.space.owner.nickname}` : "所有者待认领",
      state.space.bindings.length ? `${state.space.bindings.length} 个会话` : "未绑定会话",
      `${state.space.members.length} 位成员`,
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

    return { state, TABS, active, done, facts, overdue, setView, startCreate, onTabKeydown, toneClass };
  },
  template: `
    <header class="page-head" :class="toneClass(state.space.name)">
      <div class="cover" aria-hidden="true"></div>
      <SpaceIcon :name="state.space.name" />
      <h1 class="page-title">{{ state.space.name }}</h1>
      <p class="stats">
        <span v-for="fact in facts" :key="fact" class="stat">{{ fact }}</span>
        <span v-if="active.length" class="stat num">
          已完成 {{ done }} / {{ active.length }}<Meter :value="done / active.length" />
        </span>
        <span v-else class="stat">还没有任务</span>
        <span v-if="overdue" class="stat is-overdue num">逾期 {{ overdue }}</span>
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
