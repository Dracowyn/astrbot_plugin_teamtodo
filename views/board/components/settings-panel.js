// Space settings: one row per concern. Each row saves itself through the store (never a mix of models).
import { ref, watch } from "../vue.js";
import { LIMITS, ROLE_LABEL, describeSession, formatDateTime, formatRelative, userKey } from "../format.js";
import { ask, changeShare, removeSpace, renameSpace, resetInvite, setRole, state, unbind } from "../store.js";
import CopyField from "./copy-field.js";
import SettingsRow from "./settings-row.js";

export default {
  name: "SettingsPanel",
  components: { CopyField, SettingsRow },
  setup() {
    const name = ref(state.space.name);
    watch(() => state.space.name, (value) => { name.value = value; });

    async function confirmUnbind(binding) {
      const target = describeSession(binding.session).text;
      if (await ask({ title: "解绑此会话？", message: `「${target}」将不再使用此空间；空间和任务不受影响。`, confirmLabel: "解绑", danger: true })) {
        await unbind(binding.session);
      }
    }

    async function confirmReset() {
      const active = state.space.invite?.active;
      const message = active ? "旧邀请码会立即失效，新邀请码只在生成后显示一次。" : "新邀请码只在生成后显示一次，7 天内有效。";
      if (await ask({ title: "重置邀请码？", message, confirmLabel: "重置" })) await resetInvite();
    }

    async function confirmShareReset() {
      if (await ask({ title: "重置公开链接？", message: "旧链接会立即失效，已分享出去的人将无法再打开。", confirmLabel: "重置", danger: true })) {
        await changeShare("reset");
      }
    }

    async function confirmDelete() {
      const space = state.space;
      const confirmed = await ask({
        title: `删除空间「${space.name}」？`,
        message: `将永久删除其下的任务、操作日志、绑定、邀请码和公开链接。请输入空间名「${space.name}」确认。`,
        confirmLabel: "永久删除", danger: true, requireText: space.name,
      });
      if (confirmed) await removeSpace();
    }

    const toggleRole = (member) => setRole(member, member.role === "admin" ? "member" : "admin");
    const freshCode = () => (state.freshInviteCode?.spaceId === state.space.id ? state.freshInviteCode : null);

    return {
      state, name, LIMITS, ROLE_LABEL, userKey, describeSession, formatDateTime, formatRelative,
      renameSpace, confirmUnbind, confirmReset, confirmShareReset, confirmDelete, toggleRole, freshCode, changeShare,
    };
  },
  template: `
    <div class="settings">
      <SettingsRow title="基本信息" description="空间名在同一会话绑定的空间之间不能重名。">
        <form class="inline-form" @submit.prevent="renameSpace(name.trim())">
          <div class="field grow">
            <label for="space-name">空间名称</label>
            <input id="space-name" v-model="name" class="input" type="text" :maxlength="LIMITS.spaceName" required>
          </div>
          <button type="submit" class="btn btn-primary" :disabled="!name.trim() || name.trim() === state.space.name">保存</button>
        </form>
      </SettingsRow>

      <SettingsRow title="绑定会话" description="绑定的群聊或私聊可以在聊天里用 /todo 操作此空间。">
        <ul v-if="state.space.bindings.length" class="rows">
          <li v-for="binding in state.space.bindings" :key="binding.session" class="row">
            <div class="row-main">
              <p class="row-title">{{ describeSession(binding.session).kind }} <span class="mono">{{ describeSession(binding.session).id }}</span></p>
              <p class="hint">平台 {{ describeSession(binding.session).platform }} · 绑定于 {{ formatDateTime(binding.bound_at).slice(0, 10) }}</p>
            </div>
            <button type="button" class="btn btn-text btn-sm" @click="confirmUnbind(binding)">解绑</button>
          </li>
        </ul>
        <div v-else class="empty-block">
          <p class="row-title">没有绑定任何会话</p>
          <p class="hint">空间仍然保留，只能在这里查看和编辑。在群聊里发送 <span class="mono">/todo join 邀请码</span>，即可把群聊绑定到此空间。</p>
        </div>
      </SettingsRow>

      <SettingsRow title="成员与角色" description="成员在绑定会话里用过 todo 即自动加入。所有者由系统指定，这里只能任命或撤销管理员。">
        <table class="table">
          <caption class="visually-hidden">空间成员</caption>
          <thead><tr><th scope="col">成员</th><th scope="col">角色</th><th scope="col"><span class="visually-hidden">操作</span></th></tr></thead>
          <tbody>
            <tr v-for="member in state.space.members" :key="userKey(member)">
              <td>
                <p class="row-title">{{ member.nickname }}</p>
                <p class="hint mono">{{ member.platform_id }}:{{ member.user_id }}</p>
              </td>
              <td><span :class="member.role === 'member' ? 'flag' : 'role-strong'">{{ ROLE_LABEL[member.role] }}</span></td>
              <td class="cell-action">
                <button v-if="member.role !== 'owner'" type="button" class="btn btn-text btn-sm" @click="toggleRole(member)">
                  {{ member.role === 'admin' ? '撤销管理员' : '任命为管理员' }}
                </button>
              </td>
            </tr>
          </tbody>
        </table>
      </SettingsRow>

      <SettingsRow title="邀请码" description="其他会话用 /todo join 加入此空间。12 位随机码，7 天内可多次使用。">
        <div v-if="freshCode()" class="notice">
          <CopyField label="新邀请码（仅此一次显示）" :value="freshCode().code" />
          <p class="hint">有效至 {{ formatDateTime(freshCode().expires_at) }}。离开此页面后将无法再次查看，需要时请重置。</p>
        </div>
        <p v-else-if="state.space.invite && state.space.invite.active" class="row-title">
          当前有一个有效邀请码<span class="hint">，有效至 {{ formatDateTime(state.space.invite.expires_at) }}。出于安全，不再显示明文。</span>
        </p>
        <p v-else class="row-title">当前没有有效的邀请码</p>
        <div class="panel-actions"><button type="button" class="btn" @click="confirmReset">{{ state.space.invite && state.space.invite.active ? '重置邀请码' : '生成邀请码' }}</button></div>
      </SettingsRow>

      <SettingsRow title="公开链接" description="任何拿到链接的人都能只读查看此看板，只显示昵称，不含 ID 和操作日志。">
        <div class="switch-row">
          <div>
            <p class="row-title">公开看板 <span class="flag">{{ state.space.share.enabled ? '已开启' : '已关闭' }}</span></p>
            <p class="hint">关闭后链接立即失效；删除空间时链接同样失效。</p>
          </div>
          <button type="button" class="switch" role="switch" :aria-checked="state.space.share.enabled" aria-label="公开看板开关"
                  @click="changeShare(state.space.share.enabled ? 'off' : 'on')"><span class="switch-knob"></span></button>
        </div>
        <template v-if="state.space.share.enabled">
          <CopyField label="分享链接" :value="state.space.share.url" />
          <div class="panel-actions">
            <a class="btn" :href="state.space.share.url" target="_blank" rel="noopener noreferrer">打开预览</a>
            <button type="button" class="btn" @click="confirmShareReset">重置链接</button>
          </div>
        </template>
      </SettingsRow>

      <SettingsRow title="删除空间" description="危险操作，无法撤销。">
        <p class="row-title">删除「{{ state.space.name }}」</p>
        <p class="hint">任务、操作日志、会话绑定、邀请码和公开链接会一并删除。</p>
        <div class="panel-actions"><button type="button" class="btn btn-danger" @click="confirmDelete">删除空间…</button></div>
      </SettingsRow>
    </div>
  `,
};
