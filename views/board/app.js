// Admin view entry. Runs inside AstrBot's plugin page iframe (theme comes from <html data-theme>).
import { createApp, onMounted } from "./vue.js";
import { init, moveTask, openTask, retrySpace, startCreate, state, toggleSidebar, visibleTasks } from "./store.js";
import KanbanBoard from "./components/board.js";
import Icon from "./components/icon.js";
import ConfirmHost from "./components/confirm-host.js";
import CreateForm from "./components/create-form.js";
import EventList from "./components/event-list.js";
import FilterBar from "./components/filter-bar.js";
import InlineError from "./components/inline-error.js";
import SettingsPanel from "./components/settings-panel.js";
import AppSidebar from "./components/sidebar.js";
import SpaceHeader from "./components/space-header.js";
import TaskDrawer from "./components/task-drawer.js";
import ToastHost from "./components/toast-host.js";

const App = {
  components: { Icon, AppSidebar, ConfirmHost, CreateForm, EventList, FilterBar, InlineError, KanbanBoard, SettingsPanel, SpaceHeader, TaskDrawer, ToastHost },
  setup() {
    onMounted(init);
    const onMove = ({ seq, status }) => moveTask(seq, status);
    return { state, visibleTasks, openTask, onMove, retrySpace, startCreate, toggleSidebar };
  },
  template: `
    <div class="app">
      <AppSidebar />
      <div v-if="state.sidebarOpen" class="side-scrim" @click="toggleSidebar(false)"></div>
      <main class="main">
        <div class="topbar">
          <button type="button" class="btn btn-icon btn-text" aria-label="打开空间列表" aria-controls="sidebar" :aria-expanded="state.sidebarOpen"
                  @click="toggleSidebar()"><Icon name="menu" /></button>
          <span class="topbar-title">{{ state.space ? state.space.name : '团队待办' }}</span>
        </div>
        <p v-if="state.fatal" class="fatal" role="alert">{{ state.fatal }}</p>
        <p v-else-if="!state.ready" class="loading muted" role="status">正在加载…</p>
        <p v-else-if="state.loadingSpace" class="loading muted" role="status">正在加载空间…</p>
        <div v-else-if="state.spaceError" class="fatal load-error" role="alert">
          <p>空间加载失败：{{ state.spaceError }}</p>
          <button type="button" class="btn" @click="retrySpace">重试</button>
        </div>
        <p v-else-if="!state.space" class="loading muted">还没有任何空间。在聊天里第一次使用 /todo 时会自动创建。</p>
        <template v-else>
          <SpaceHeader />
          <div id="view-panel" role="tabpanel" :aria-labelledby="'tab-' + state.view" class="view">
            <template v-if="state.view === 'board'">
              <CreateForm v-if="state.creating" />
              <FilterBar />
              <InlineError scope="board" />
              <KanbanBoard :tasks="visibleTasks" :now="state.now" :show-archived="state.showArchived" editable
                           @open="openTask" @move="onMove" @add="startCreate" />
            </template>
            <section v-else-if="state.view === 'log'" class="log-view" aria-label="操作日志">
              <p v-if="state.pending.log" class="muted" role="status">正在加载日志…</p>
              <InlineError scope="log" />
              <EventList v-if="!state.pending.log && !state.errors.log" :events="state.events" :now="state.now" show-task grouped empty-text="这个空间还没有操作记录" />
            </section>
            <SettingsPanel v-else :key="state.spaceId" />
          </div>
        </template>
      </main>
      <TaskDrawer v-if="state.drawer" :key="state.drawer.seq" />
      <ConfirmHost />
      <ToastHost />
    </div>
  `,
};

createApp(App).mount("#app");
