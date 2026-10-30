// Left navigation like a Notion page tree: brand row, then one row per space (icon tile, name, open-task count).
// Below 900px it becomes a drawer opened from the top bar (see app.js).
import { selectSpace, state, toggleSidebar } from "../store.js";
import { initial, tagColor } from "../format.js";

export default {
  name: "AppSidebar",
  setup() {
    const openCount = (space) => space.task_counts.todo + space.task_counts.doing;
    return { state, selectSpace, toggleSidebar, openCount, initial, tagColor };
  },
  template: `
    <nav id="sidebar" class="sidebar" :class="{ 'is-open': state.sidebarOpen }" aria-label="空间">
      <div class="side-brand">
        <p class="side-name">团队待办</p>
        <p class="side-scope">管理视图</p>
      </div>
      <h2 class="side-label">空间</h2>
      <ul class="space-list">
        <li v-for="space in state.spaces" :key="space.id">
          <button type="button" class="space-item" :aria-current="space.id === state.spaceId ? 'page' : undefined"
                  @click="selectSpace(space.id)">
            <span class="side-icon" :class="'c-' + tagColor(space.name)" aria-hidden="true">{{ initial(space.name) }}</span>
            <span class="space-item-name">{{ space.name }}</span>
            <span class="space-item-count num" :title="'未完成 ' + openCount(space) + ' 项'">{{ openCount(space) || '' }}</span>
          </button>
        </li>
      </ul>
    </nav>
  `,
};
