// Board filters: assignee, tag, archived switch. Filtering is client-side over the loaded tasks.
import { userKey } from "../format.js";
import { allTags, hasFilters, members, setShowArchived, state, visibleTasks } from "../store.js";

export default {
  name: "FilterBar",
  setup() {
    function clear() {
      state.filters.assignee = "";
      state.filters.tag = "";
    }
    return { state, members, allTags, hasFilters, visibleTasks, userKey, setShowArchived, clear };
  },
  template: `
    <div class="filters" role="group" aria-label="筛选">
      <div class="filter">
        <label for="f-assignee">负责人</label>
        <select id="f-assignee" v-model="state.filters.assignee" class="select">
          <option value="">全部</option>
          <option v-for="m in members" :key="userKey(m)" :value="userKey(m)">{{ m.nickname }}</option>
        </select>
      </div>
      <div class="filter">
        <label for="f-tag">标签</label>
        <select id="f-tag" v-model="state.filters.tag" class="select">
          <option value="">全部</option>
          <option v-for="tag in allTags" :key="tag" :value="tag">{{ tag }}</option>
        </select>
      </div>
      <button v-if="hasFilters" type="button" class="btn btn-text" @click="clear">清除筛选</button>
      <label class="check">
        <input type="checkbox" :checked="state.showArchived" @change="setShowArchived($event.target.checked)">
        显示已归档
      </label>
      <p class="filters-count num" aria-live="polite">显示 {{ visibleTasks.length }} 项</p>
    </div>
  `,
};
