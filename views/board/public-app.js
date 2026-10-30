// Public read-only board. In M4 the plugin's own HTTP server serves this file and the shared
// components from the same directory; until then it runs on mock data (token from ?t=).
import { computed, createApp, onBeforeUnmount, onMounted, ref } from "./vue.js";
import { getPublicBoard } from "./api.js";
import { STATUSES, formatRelative, initial, nowSeconds, setTimeZone, tagColor } from "./format.js";
import KanbanBoard from "./components/board.js";
import RefreshIndicator from "./components/refresh-indicator.js";

const REFRESH_SECONDS = 30;
const TOKEN_IN_PATH = /\/b\/([^/]+)/;

/** Token comes from the path (`/b/<token>/`) in production and from `?t=` in the demo. */
function readToken() {
  const fromPath = TOKEN_IN_PATH.exec(location.pathname);
  return fromPath ? decodeURIComponent(fromPath[1]) : new URLSearchParams(location.search).get("t") || "";
}

const App = {
  components: { KanbanBoard, RefreshIndicator },
  setup() {
    const board = ref(null);
    const state = ref("loading"); // loading | ok | error
    const errorMessage = ref("");
    const refreshFailed = ref(false);
    const refreshing = ref(false);
    const cycle = ref(0);
    const now = ref(nowSeconds());
    const activeColumn = ref(0);
    const boardWrap = ref(null);
    const token = readToken();
    let pollTimer = null;
    let clockTimer = null;
    let lastLoad = 0;
    const linkClosed = ref(false); // a closed or unknown link will not come back by itself; transient failures keep polling

    const counts = computed(() => {
      const result = { todo: 0, doing: 0, done: 0 };
      (board.value?.tasks || []).forEach((task) => { result[task.status] += 1; });
      return result;
    });
    const total = computed(() => counts.value.todo + counts.value.doing + counts.value.done);

    async function load() {
      clearTimeout(pollTimer);
      refreshing.value = state.value === "ok";
      try {
        const data = await getPublicBoard(token);
        setTimeZone(data.timezone);
        board.value = data;
        state.value = "ok";
        refreshFailed.value = false;
        document.title = `${data.space.name} · 团队待办`;
      } catch (err) {
        if (state.value === "ok") refreshFailed.value = true;
        else {
          state.value = "error";
          linkClosed.value = err?.status === 404;
          errorMessage.value = linkClosed.value ? "链接无效或已关闭" : "暂时无法加载，请稍后重试";
        }
      } finally {
        refreshing.value = false;
        lastLoad = Date.now();
        now.value = nowSeconds();
        cycle.value += 1;
        if (!linkClosed.value) pollTimer = setTimeout(load, REFRESH_SECONDS * 1000);
      }
    }

    function onVisibility() {
      if (document.visibilityState === "visible" && Date.now() - lastLoad > REFRESH_SECONDS * 1000) load();
    }

    function columnElements() {
      return boardWrap.value?.querySelectorAll(".col") ?? [];
    }

    function goToColumn(index) {
      const reduced = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
      columnElements()[index]?.scrollIntoView({ behavior: reduced ? "auto" : "smooth", inline: "start", block: "nearest" });
    }

    function onBoardScroll(event) {
      const strip = event.target;
      if (!strip.classList?.contains("board")) return;
      const offsets = [...columnElements()].map((col) => Math.abs(col.offsetLeft - strip.offsetLeft - strip.scrollLeft));
      activeColumn.value = offsets.indexOf(Math.min(...offsets));
    }

    onMounted(() => {
      load();
      clockTimer = setInterval(() => { now.value = nowSeconds(); }, 30000);
      document.addEventListener("visibilitychange", onVisibility);
    });
    onBeforeUnmount(() => {
      clearTimeout(pollTimer);
      clearInterval(clockTimer);
      document.removeEventListener("visibilitychange", onVisibility);
    });

    return {
      board, state, errorMessage, linkClosed, refreshFailed, refreshing, cycle, now, counts, total, activeColumn, boardWrap,
      STATUSES, REFRESH_SECONDS, formatRelative, initial, tagColor, goToColumn, onBoardScroll, load,
    };
  },
  template: `
    <div class="pub">
      <div class="pub-cover" aria-hidden="true"></div>
      <header class="pub-head">
        <span class="page-icon pub-icon" :class="'c-' + tagColor(board ? board.space.name : 'x')" aria-hidden="true">{{ board ? initial(board.space.name) : '·' }}</span>
        <h1 class="pub-title">{{ board ? board.space.name : state === 'error' ? '无法打开看板' : '加载中…' }}</h1>
        <p v-if="board" class="pub-meta">
          <span class="num">共 {{ total }} 项</span>
          <span class="num">进行中 {{ counts.doing }}</span>
          <span class="num">已完成 {{ counts.done }}</span>
          <span>更新于 <time class="num">{{ formatRelative(board.updated_at, now) }}</time></span>
        </p>
      </header>

      <main class="pub-main" ref="boardWrap" @scroll.capture="onBoardScroll">
        <div v-if="state === 'error'" class="pub-state" role="alert">
          <p class="pub-state-title">{{ errorMessage }}</p>
          <p class="muted">请向分享人确认链接是否仍然有效。</p>
          <button v-if="!linkClosed" type="button" class="btn" @click="load">重试</button>
        </div>
        <p v-else-if="state === 'loading'" class="pub-state muted" role="status">正在加载看板…</p>
        <template v-else>
          <nav class="pub-tabs" aria-label="切换列">
            <button v-for="(column, index) in STATUSES" :key="column.key" type="button" class="pub-tab"
                    :aria-current="activeColumn === index ? 'true' : undefined" @click="goToColumn(index)">
              {{ column.label }} <span class="num pub-tab-count">{{ counts[column.key] }}</span>
            </button>
          </nav>
          <KanbanBoard :tasks="board.tasks" :now="now" :editable="false" />
        </template>
      </main>

      <footer class="pub-foot">
        <p class="pub-foot-line"><span>只读看板</span><span aria-hidden="true">·</span><RefreshIndicator v-if="board" :cycle="cycle" :seconds="REFRESH_SECONDS" :refreshing="refreshing" :failed="refreshFailed" /><span v-else>每 {{ REFRESH_SECONDS }} 秒刷新</span></p>
        <p class="pub-foot-note">仅显示昵称。需要修改请在群里使用 /todo 命令。</p>
      </footer>
    </div>
  `,
};

createApp(App).mount("#app");
