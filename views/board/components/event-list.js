// Operation log list. In the side peek it is a quiet per-task history; on the log tab (`grouped`) it is split
// under day headings. The server renders `summary` and `actor_name`; this component only lays them out.
import { computed } from "../vue.js";
import { formatClock, formatDateTime, formatDayHeading, formatRelative } from "../format.js";
import Avatar from "./avatar.js";

export default {
  name: "EventList",
  components: { Avatar },
  props: {
    events: { type: Array, required: true },
    now: { type: Number, required: true },
    showTask: { type: Boolean, default: false },
    grouped: { type: Boolean, default: false },
    emptyText: { type: String, default: "还没有操作记录" },
  },
  setup(props) {
    const timeText = (event) => (props.grouped ? formatClock(event.created_at) : formatRelative(event.created_at, props.now));
    const groups = computed(() => {
      const result = [];
      props.events.forEach((event) => {
        const label = props.grouped ? formatDayHeading(event.created_at, props.now) : "";
        const last = result[result.length - 1];
        if (last && last.label === label) last.events.push(event);
        else result.push({ label, events: [event] });
      });
      return result;
    });
    return { groups, timeText, formatDateTime };
  },
  template: `
    <div v-if="events.length" class="activity">
      <section v-for="group in groups" :key="group.label" class="activity-group">
        <h3 v-if="group.label" class="day-head">{{ group.label }}</h3>
        <ol>
          <li v-for="(event, index) in group.events" :key="index" class="act">
            <Avatar :name="event.actor_name" />
            <div class="act-body">
              <p class="act-line">
                <strong class="act-actor">{{ event.actor_name }}</strong>
                <span v-if="showTask && event.task_seq != null" class="act-seq num">#{{ event.task_seq }}</span>
                <span v-else-if="showTask" class="act-seq">空间</span>
                {{ event.summary }}
              </p>
            </div>
            <time class="act-time num" :datetime="new Date(event.created_at * 1000).toISOString()" :title="formatDateTime(event.created_at)">{{ timeText(event) }}</time>
          </li>
        </ol>
      </section>
    </div>
    <p v-else class="empty-note">{{ emptyText }}</p>
  `,
};
