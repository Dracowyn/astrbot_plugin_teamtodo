// Left navigation: product mark, then one row per space (tone icon tile, name, open-task count).
// Below 900px it becomes a drawer opened from the top bar (see app.js).
import { selectSpace, state, toggleSidebar } from "../store.js";
import { toneClass } from "../format.js";
import Icon from "./icon.js";
import SpaceIcon from "./space-icon.js";

export default {
  name: "AppSidebar",
  components: { Icon, SpaceIcon },
  setup() {
    const openCount = (space) => space.task_counts.todo + space.task_counts.doing;
    return { state, selectSpace, toggleSidebar, openCount, toneClass };
  },
  template: `
    <nav id="sidebar" class="sidebar" :class="{ 'is-open': state.sidebarOpen }" aria-label="空间">
      <div class="side-brand">
        <span class="side-mark" aria-hidden="true"><Icon name="check" /></span>
        <div>
          <p class="side-name">团队待办</p>
          <p class="side-scope">管理视图</p>
        </div>
      </div>
      <h2 class="side-label">空间</h2>
      <ul class="space-list">
        <li v-for="space in state.spaces" :key="space.id" :class="toneClass(space.name)">
          <button type="button" class="space-item" :aria-current="space.id === state.spaceId ? 'page' : undefined"
                  @click="selectSpace(space.id)">
            <SpaceIcon :name="space.name" />
            <span class="space-item-name">{{ space.name }}</span>
            <span class="space-item-count num" :title="'未完成 ' + openCount(space) + ' 项'">{{ openCount(space) || '' }}</span>
          </button>
        </li>
      </ul>
    </nav>
  `,
};
