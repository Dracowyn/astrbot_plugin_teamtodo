// Admin view entry. Runs inside AstrBot's plugin page iframe (theme comes from <html data-theme>).
import { createApp, onMounted } from "./vue.js";
import { init, moveTask, openTask, state, visibleTasks } from "./store.js";
import KanbanBoard from "./components/board.js";
import ConfirmHost from "./components/confirm-host.js";
import CreateForm from "./components/create-form.js";
import EventList from "./components/event-list.js";
import FilterBar from "./components/filter-bar.js";
import SettingsPanel from "./components/settings-panel.js";
import AppSidebar from "./components/sidebar.js";
import SpaceHeader from "./components/space-header.js";
import TaskDrawer from "./components/task-drawer.js";
import ToastHost from "./components/toast-host.js";

const App = {
  components: { AppSidebar, ConfirmHost, CreateForm, EventList, FilterBar, KanbanBoard, SettingsPanel, SpaceHeader, TaskDrawer, ToastHost },
  setup() {
    onMounted(init);
    const onMove = ({ seq, status }) => moveTask(seq, status);
    return { state, visibleTasks, openTask, onMove };
  },
  template: `
    <div class="app">
      <AppSidebar />
      <main class="main">
        <p v-if="state.fatal" class="fatal" role="alert">{{ state.fatal }}</p>
        <p v-else-if="!state.ready" class="loading muted" role="status">正在加载…</p>
        <p v-else-if="!state.space" class="loading muted">还没有任何空间。在聊天里第一次使用 /todo 时会自动创建。</p>
        <template v-else>
          <SpaceHeader />
          <div id="view-panel" role="tabpanel" :aria-labelledby="'tab-' + state.view" class="view">
            <template v-if="state.view === 'board'">
              <CreateForm />
              <FilterBar />
              <KanbanBoard :tasks="visibleTasks" :now="state.now" :show-archived="state.showArchived" editable
                           @open="openTask" @move="onMove" />
            </template>
            <section v-else-if="state.view === 'log'" class="log-view" aria-label="操作日志">
              <EventList :events="state.events" :now="state.now" show-task empty-text="这个空间还没有操作记录" />
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
