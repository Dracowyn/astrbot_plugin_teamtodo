// Space settings: one row per concern. Each row saves itself through the store and shows its own failure
// (InlineError scope) right under the action. Share links are M4 and intentionally absent.
import { ref, watch } from "../vue.js";
import { LIMITS, ROLE_COLOR, ROLE_LABEL, describeSession, formatDateTime, userKey } from "../format.js";
import { ask, removeSpace, renameSpace, resetInvite, setRole, state, syncMembers, unbind } from "../store.js";
import CopyField from "./copy-field.js";
import InlineError from "./inline-error.js";
import Pill from "./pill.js";
import SettingsRow from "./settings-row.js";

export default {
  name: "SettingsPanel",
  components: { CopyField, InlineError, Pill, SettingsRow },
  setup() {
    const name = ref(state.space.name);
    watch(() => state.space.name, (value) => { name.value = value; });

    async function confirmUnbind(binding) {
      const target = binding.title || describeSession(binding.session).text;
      if (await ask({ title: "解绑此会话？", message: `「${target}」将不再使用此空间；空间和任务不受影响。`, confirmLabel: "解绑", danger: true })) {
        await unbind(binding.session);
      }
    }

    async function confirmReset() {
      const message = state.space.invite ? "旧邀请码会立即失效，新邀请码只在生成后显示一次。" : "新邀请码只在生成后显示一次。";
      if (await ask({ title: "重置邀请码？", message, confirmLabel: "重置" })) await resetInvite();
    }

    async function confirmDelete() {
      const space = state.space;
      const confirmed = await ask({
        title: `删除空间「${space.name}」？`,
        message: `将永久删除空间「${space.name}」及其全部任务、操作日志和会话绑定，且无法恢复。`,
        confirmLabel: "永久删除", danger: true,
      });
      if (confirmed) await removeSpace();
    }

    const toggleRole = (member) => setRole(member, member.role === "admin" ? "member" : "admin");
    const freshCode = () => (state.freshInviteCode?.spaceId === state.space.id ? state.freshInviteCode : null);
    const inviteExpired = () => state.space.invite && state.space.invite.expires_at < state.now;

    return {
      state, name, LIMITS, ROLE_COLOR, ROLE_LABEL, userKey, describeSession, formatDateTime,
      renameSpace, syncMembers, confirmUnbind, confirmReset, confirmDelete, toggleRole, freshCode, inviteExpired,
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
          <button type="submit" class="btn btn-primary" :disabled="!name.trim() || name.trim() === state.space.name || state.pending.rename">{{ state.pending.rename ? '保存中…' : '保存' }}</button>
        </form>
        <InlineError scope="rename" />
      </SettingsRow>

      <SettingsRow title="绑定会话" description="绑定的群聊或私聊可以在聊天里用 /todo 操作此空间。">
        <ul v-if="state.space.bindings.length" class="rows">
          <li v-for="binding in state.space.bindings" :key="binding.session" class="row">
            <div class="row-main">
              <p class="row-title">{{ binding.title || describeSession(binding.session).text }}</p>
              <p v-if="binding.title" class="hint mono">{{ binding.session }}</p>
              <p class="hint">绑定于 {{ formatDateTime(binding.bound_at).slice(0, 10) }}</p>
            </div>
            <button type="button" class="btn btn-text btn-sm" :disabled="state.pending.bindings" @click="confirmUnbind(binding)">解绑</button>
          </li>
        </ul>
        <div v-else class="empty-block">
          <p class="row-title">没有绑定任何会话</p>
          <p class="hint">空间仍然保留，只能在这里查看和编辑。在群聊里发送 <span class="mono">/todo join 邀请码</span>，即可把群聊绑定到此空间。</p>
        </div>
        <InlineError scope="bindings" />
      </SettingsRow>

      <SettingsRow title="成员与角色" description="成员在绑定会话里用过 todo 即自动加入。所有者由系统指定，这里只能任命或撤销管理员。">
        <div class="panel-actions"><button type="button" class="btn" :disabled="!state.space.bindings.length || state.pending.sync" @click="syncMembers">{{ state.pending.sync ? '同步中…' : '从群同步成员' }}</button></div>
        <p class="hint">{{ state.space.bindings.length ? '从绑定的 QQ 群拉取成员列表，加入本空间；不会移除成员，也不改角色。' : '此空间没有绑定会话，无法同步；先在上方绑定群聊。' }}</p>
        <InlineError scope="sync" />
        <div v-if="state.syncResult" class="notice" role="status">
          <ul class="sync-results">
            <li v-for="r in state.syncResult.results" :key="r.session">
              <span>{{ r.title || describeSession(r.session).text }}</span>
              <span v-if="r.error" class="form-error is-bare">{{ r.error }}</span>
              <span v-else class="hint">新增 {{ r.added }} 人</span>
            </li>
          </ul>
          <p class="row-title">共新增 {{ state.syncResult.added }} 人</p>
        </div>
        <div class="scroll-box">
        <table class="table">
          <caption class="visually-hidden">空间成员</caption>
          <thead><tr><th scope="col">成员</th><th scope="col">角色</th><th scope="col"><span class="visually-hidden">操作</span></th></tr></thead>
          <tbody>
            <tr v-for="member in state.space.members" :key="userKey(member)">
              <td>
                <p class="row-title">{{ member.nickname }}</p>
                <p class="hint mono">{{ member.platform_id }}:{{ member.user_id }}</p>
              </td>
              <td><Pill :color="ROLE_COLOR[member.role]">{{ ROLE_LABEL[member.role] }}</Pill></td>
              <td class="cell-action">
                <button v-if="member.role !== 'owner'" type="button" class="btn btn-text btn-sm" :disabled="state.pending.members" @click="toggleRole(member)">
                  {{ member.role === 'admin' ? '撤销管理员' : '任命为管理员' }}
                </button>
              </td>
            </tr>
          </tbody>
        </table>
        </div>
        <InlineError scope="members" />
      </SettingsRow>

      <SettingsRow title="邀请码" description="其他会话用 /todo join 加入此空间。">
        <div v-if="freshCode()" class="notice">
          <CopyField label="新邀请码" :value="freshCode().code" />
          <p class="hint">有效至 {{ formatDateTime(freshCode().expires_at) }}。此码只显示这一次，离开此页面后无法再次查看；请现在复制保存。</p>
        </div>
        <p v-else-if="state.space.invite" class="row-title">
          {{ inviteExpired() ? '邀请码已过期' : '当前有一个有效邀请码' }}<span class="hint">，有效至 {{ formatDateTime(state.space.invite.expires_at) }}。出于安全，不显示明文。</span>
        </p>
        <p v-else class="row-title">当前没有有效的邀请码</p>
        <div class="panel-actions"><button type="button" class="btn" :disabled="state.pending.invite" @click="confirmReset">{{ state.pending.invite ? '处理中…' : (state.space.invite ? '重置邀请码' : '生成邀请码') }}</button></div>
        <InlineError scope="invite" />
      </SettingsRow>

      <SettingsRow title="删除空间" description="危险操作，无法撤销。">
        <p class="row-title">删除「{{ state.space.name }}」</p>
        <p class="hint">任务、操作日志和会话绑定会一并永久删除。</p>
        <div class="panel-actions"><button type="button" class="btn btn-danger" :disabled="state.pending.delete" @click="confirmDelete">{{ state.pending.delete ? '删除中…' : '删除空间…' }}</button></div>
        <InlineError scope="delete" />
      </SettingsRow>
    </div>
  `,
};
