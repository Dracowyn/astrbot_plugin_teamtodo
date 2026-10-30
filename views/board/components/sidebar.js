// Left navigation: wordmark and a plain list of spaces. The selected space is marked by weight and an
// accent bar; the second line is muted text (sessions · members · overdue).
import { selectSpace, state } from "../store.js";

export default {
  name: "AppSidebar",
  setup() {
    return { state, selectSpace };
  },
  template: `
    <nav class="sidebar" aria-label="空间">
      <div class="side-brand">
        <p class="side-name">团队待办</p>
        <p class="side-scope">管理视图</p>
      </div>
      <h2 class="side-label">空间</h2>
      <ul class="space-list">
        <li v-for="space in state.spaces" :key="space.id">
          <button type="button" class="space-item" :aria-current="space.id === state.spaceId ? 'page' : undefined"
                  @click="selectSpace(space.id)">
            <span class="space-item-name">{{ space.name }}</span>
            <span class="space-item-sub num">
              <span>{{ space.binding_count ? space.binding_count + ' 个会话' : '未绑定' }}</span>
              <span>{{ space.member_count }} 人</span>
              <span v-if="space.overdue_count" class="is-overdue">{{ space.overdue_count }} 逾期</span>
            </span>
          </button>
        </li>
      </ul>
    </nav>
  `,
};
