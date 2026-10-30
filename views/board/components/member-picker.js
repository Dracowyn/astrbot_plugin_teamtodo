// Assignee chooser as a Notion people property: a cell of avatar chips that opens a filterable member popover.
import { computed, nextTick, onBeforeUnmount, ref, watch } from "../vue.js";
import { LIMITS, userKey } from "../format.js";
import Avatar from "./avatar.js";
import Icon from "./icon.js";

let uid = 0;
const POP_GAP = 4;
const POP_MARGIN = 8;
const POP_MAX_HEIGHT = 340;

export default {
  name: "MemberPicker",
  components: { Avatar, Icon },
  props: {
    members: { type: Array, required: true },
    modelValue: { type: Array, required: true }, // array of identity keys
  },
  emits: ["update:modelValue"],
  setup(props, { emit }) {
    const id = `mp${++uid}`;
    const open = ref(false);
    const query = ref("");
    const active = ref(0);
    const root = ref(null);
    const trigger = ref(null);
    const search = ref(null);
    const popStyle = ref({});

    const atLimit = computed(() => props.modelValue.length >= LIMITS.assignees);
    // Selected keys that are no longer members are still shown (and removable) so they never hide silently.
    const selected = computed(() =>
      props.modelValue.map((key) => props.members.find((m) => userKey(m) === key) ?? { nickname: key, user_id: key, platform_id: "" }),
    );
    const filtered = computed(() => {
      const q = query.value.trim().toLowerCase();
      return q ? props.members.filter((m) => m.nickname.toLowerCase().includes(q)) : props.members;
    });

    function isSelected(member) { return props.modelValue.includes(userKey(member)); }

    function toggle(member) {
      const key = userKey(member);
      if (props.modelValue.includes(key)) emit("update:modelValue", props.modelValue.filter((k) => k !== key));
      else if (!atLimit.value) {
        // Keep member-list order (what the server returns) so a saved selection compares equal to the draft.
        const keys = new Set([...props.modelValue, key]);
        const ordered = props.members.map(userKey).filter((k) => keys.has(k));
        emit("update:modelValue", [...ordered, ...props.modelValue.filter((k) => !ordered.includes(k))]);
      }
    }
    function remove(member) { emit("update:modelValue", props.modelValue.filter((k) => k !== userKey(member))); }

    // Fixed positioning escapes the peek's scroll container; flip above the trigger when there is no room below.
    function place() {
      if (!trigger.value) return;
      const rect = trigger.value.getBoundingClientRect();
      const width = Math.min(280, window.innerWidth - POP_MARGIN * 2);
      const left = Math.max(POP_MARGIN, Math.min(rect.left, window.innerWidth - width - POP_MARGIN));
      const below = window.innerHeight - rect.bottom - POP_GAP - POP_MARGIN;
      const above = rect.top - POP_GAP - POP_MARGIN;
      const flip = below < 200 && above > below;
      const maxHeight = Math.min(POP_MAX_HEIGHT, flip ? above : below);
      popStyle.value = {
        left: `${left}px`,
        width: `${width}px`,
        maxHeight: `${maxHeight}px`,
        ...(flip ? { bottom: `${window.innerHeight - rect.top + POP_GAP}px` } : { top: `${rect.bottom + POP_GAP}px` }),
      };
    }
    function onOutside(event) { if (!root.value?.contains(event.target)) close(false); }

    async function show() {
      query.value = "";
      active.value = 0;
      place();
      open.value = true;
      await nextTick();
      search.value?.focus();
    }
    function close(returnFocus = true) {
      if (!open.value) return;
      open.value = false;
      if (returnFocus) trigger.value?.focus();
    }

    watch(open, (isOpen) => {
      const method = isOpen ? "addEventListener" : "removeEventListener";
      document[method]("pointerdown", onOutside, true);
      window[method]("resize", place);
      window[method]("scroll", place, true);
    });
    watch(query, () => { active.value = 0; });
    onBeforeUnmount(() => {
      document.removeEventListener("pointerdown", onOutside, true);
      window.removeEventListener("resize", place);
      window.removeEventListener("scroll", place, true);
    });

    function onKeydown(event) {
      if (!open.value) return;
      if (event.key === "Escape") { event.stopPropagation(); event.preventDefault(); close(); return; }
      if (event.key === "Tab") { close(false); return; }
      const last = filtered.value.length - 1;
      if (event.key === "ArrowDown" || event.key === "ArrowUp") {
        event.preventDefault();
        if (last < 0) return;
        active.value = event.key === "ArrowDown" ? Math.min(active.value + 1, last) : Math.max(active.value - 1, 0);
        nextTick(() => document.getElementById(`${id}-o${active.value}`)?.scrollIntoView({ block: "nearest" }));
      } else if (event.key === "Enter" && event.target === search.value) {
        event.preventDefault(); // never submits the surrounding form
        if (filtered.value[active.value]) toggle(filtered.value[active.value]);
      }
    }

    return {
      id, open, query, active, root, trigger, search, popStyle, atLimit, selected, filtered, LIMITS,
      isSelected, toggle, remove, show, close, onKeydown, userKey,
    };
  },
  template: `
    <div ref="root" class="mp" @keydown="onKeydown">
      <button ref="trigger" type="button" class="mp-trigger" aria-haspopup="listbox" :aria-expanded="open" :aria-controls="id + '-pop'"
              aria-label="负责人" @click="open ? close() : show()">
        <template v-if="selected.length">
          <span v-for="m in selected" :key="userKey(m)" class="chip"><Avatar :name="m.nickname" />{{ m.nickname }}</span>
        </template>
        <span v-else class="mp-empty">空</span>
      </button>
      <div v-if="open" :id="id + '-pop'" class="popover" :style="popStyle">
        <ul v-if="selected.length" class="mp-selected" aria-label="已选负责人" @mousedown.prevent>
          <li v-for="m in selected" :key="userKey(m)" class="chip"><Avatar :name="m.nickname" />{{ m.nickname }}<button type="button" class="chip-x" :aria-label="'移除 ' + m.nickname" @click="remove(m)"><Icon name="close" /></button></li>
        </ul>
        <input ref="search" v-model="query" class="mp-search" type="text" role="combobox" aria-expanded="true" :aria-controls="id + '-list'"
               :aria-activedescendant="filtered.length ? id + '-o' + active : undefined" placeholder="搜索成员…" aria-label="搜索成员" autocomplete="off">
        <p v-if="atLimit" class="hint mp-note">最多 {{ LIMITS.assignees }} 人，已达上限</p>
        <ul :id="id + '-list'" class="mp-list" role="listbox" aria-multiselectable="true" aria-label="空间成员" @mousedown.prevent>
          <li v-for="(m, i) in filtered" :id="id + '-o' + i" :key="userKey(m)" class="mp-option" :class="{ 'is-active': i === active }"
              role="option" :aria-selected="isSelected(m)" :aria-disabled="atLimit && !isSelected(m)" @click="toggle(m)" @mousemove="active = i">
            <Avatar :name="m.nickname" /><span class="mp-name">{{ m.nickname }}</span><Icon v-if="isSelected(m)" name="check" />
          </li>
        </ul>
        <p v-if="!members.length" class="hint mp-note">空间还没有成员</p>
        <p v-else-if="!filtered.length" class="hint mp-note">没有匹配的成员</p>
      </div>
    </div>
  `,
};
