// Subtle auto-refresh indicator: a ring that fills over one polling interval, restarted on every cycle.
// With prefers-reduced-motion the ring is static and the text carries the information.
const RADIUS = 6;
const CIRCUMFERENCE = 2 * Math.PI * RADIUS;

export default {
  name: "RefreshIndicator",
  props: {
    cycle: { type: Number, required: true },
    seconds: { type: Number, required: true },
    refreshing: { type: Boolean, default: false },
    failed: { type: Boolean, default: false },
  },
  setup() {
    return { RADIUS, CIRCUMFERENCE };
  },
  template: `
    <span class="refresh" role="status" :class="{ 'is-failed': failed, 'is-busy': refreshing }">
      <svg class="refresh-ring" width="16" height="16" viewBox="0 0 16 16" aria-hidden="true" focusable="false">
        <circle class="refresh-track" cx="8" cy="8" :r="RADIUS" />
        <circle :key="cycle" class="refresh-arc" cx="8" cy="8" :r="RADIUS" transform="rotate(-90 8 8)"
                :style="{ strokeDasharray: CIRCUMFERENCE, animationDuration: seconds + 's' }" />
      </svg>
      <span>{{ failed ? '刷新失败，稍后重试' : refreshing ? '刷新中…' : '每 ' + seconds + ' 秒刷新' }}</span>
    </span>
  `,
};
