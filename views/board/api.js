// Data access for both pages. Every function is async and returns plain JSON-shaped objects.
// In this demo `send()` talks to the in-memory mock (mock.js). In M3 only `send()` changes:
//   GET  -> window.AstrBotPluginView.apiGet(endpoint, params)    (no leading slash, no query string)
//   POST -> window.AstrBotPluginView.apiPost(endpoint, body)
// and the public page calls fetch() against the M4 server (`b/<token>/api/board`, see getPublicBoard).
// Failures reject with Error(message) where `message` is user-facing Chinese text (like TodoError) and
// `status` is the HTTP status when known.
//
// ---- Proposed REST contract (field names mirror core/models.py; times are Unix seconds, UTC) ----
//
// User     = { platform_id: string, user_id: string, nickname: string }       // UserRef + display name
// Task     = { space_id, seq, title, status: "todo"|"doing"|"done"|"archived", assignees: User[],
//              due_at: number|null, tags: string[], priority: "high"|"medium"|"low"|null,
//              description: string, creator: User, created_session: string,
//              created_at, updated_at, done_at: number|null, reminded: boolean }
//              // The global row id is never exposed; a task is addressed by (space_id, seq).
// Member   = User & { role: "owner"|"admin"|"member", joined_at }
// Binding  = { session: string /* unified_msg_origin: "<platform>:<GroupMessage|FriendMessage>:<id>" */, bound_at }
// Event    = { id, space_id, task_seq, action: "create"|"update"|"status"|"assign"|"delete"|"archive",
//              detail: object, actor: User|null, actor_label: string /* WebUI user name */, session, created_at }
//              // detail by action: create {title}; update {field: [old, new]}; status {status: [old, new]};
//              // assign {added: nickname[], removed: nickname[]}; archive {reason: "auto"|"manual"};
//              // delete {snapshot: <task before deletion>}.
// SpaceSummary = { id, name, owner: User|null, created_at, binding_count, member_count,
//              task_counts: {todo, doing, done, archived}, overdue_count, shared: boolean }
// SpaceDetail  = SpaceSummary & { bindings: Binding[], members: Member[],
//              invite: {active: boolean, expires_at}|null,        // the code itself is never returned again
//              share: {enabled: boolean, url: string|null} }
// PublicTask   = { seq, title, status, assignees: {nickname}[], due_at, tags, priority, description,
//              done_at, updated_at }                               // whitelist: no IDs, no creator, no log
//
// Endpoints (relative to the plugin's web API prefix):
//   getMeta()                                GET  meta                      -> { timezone, server_time, auto_archive_days, limits }
//   listSpaces()                             GET  spaces                    -> { spaces: SpaceSummary[] }
//   getSpace(spaceId)                        GET  spaces/{id}               -> SpaceDetail
//   renameSpace(spaceId, name)               POST spaces/{id}/rename        {name}              -> SpaceDetail
//   deleteSpace(spaceId)                     POST spaces/{id}/delete        {}                  -> { ok: true }
//   unbindSession(spaceId, session)          POST spaces/{id}/unbind        {session}           -> SpaceDetail
//   setMemberRole(spaceId, user, role)       POST spaces/{id}/members/role  {platform_id, user_id, role: "admin"|"member"} -> SpaceDetail
//   resetInvite(spaceId)                     POST spaces/{id}/invite/reset  {}                  -> { code: string(12), expires_at }   // code shown once
//   setShare(spaceId, action)                POST spaces/{id}/share         {action: "on"|"off"|"reset"} -> { enabled, url }
//   listTasks(spaceId, includeArchived)      GET  spaces/{id}/tasks         ?include_archived=1 -> { tasks: Task[], server_time }
//   createTask(spaceId, fields)              POST spaces/{id}/tasks         {title, assignees?: {platform_id,user_id}[], due_at?, tags?, priority?, description?} -> Task
//   getTask(spaceId, seq)                    GET  spaces/{id}/tasks/{seq}   -> { task: Task, events: Event[] (newest first) }
//   updateTask(spaceId, seq, patch)          POST spaces/{id}/tasks/{seq}   partial {title, description, status, assignees, due_at, tags, priority} -> Task
//   deleteTask(spaceId, seq)                 POST spaces/{id}/tasks/{seq}/delete {}             -> { ok: true }
//   listEvents(spaceId, limit)               GET  spaces/{id}/events        ?limit=50           -> { events: Event[] (newest first) }
//   getPublicBoard(token)                    GET  b/{token}/api/board       (M4 server, no login) -> { space: {name}, tasks: PublicTask[],
//                                                                              updated_at, server_time, timezone }; unknown/closed token -> 404 "链接无效或已关闭"
// Limits (title 200, description 4000, tag 20, tags 10, assignees 20, space name 30) are enforced server side.

import { request } from "./mock.js";

function send(method, endpoint, payload) {
  return request(method, endpoint, payload);
}

const get = (endpoint, params) => send("GET", endpoint, params);
const post = (endpoint, body) => send("POST", endpoint, body);
const ref = ({ platform_id, user_id }) => ({ platform_id, user_id });

export const getMeta = () => get("meta");
export const listSpaces = () => get("spaces");
export const getSpace = (spaceId) => get(`spaces/${spaceId}`);
export const renameSpace = (spaceId, name) => post(`spaces/${spaceId}/rename`, { name });
export const deleteSpace = (spaceId) => post(`spaces/${spaceId}/delete`, {});
export const unbindSession = (spaceId, session) => post(`spaces/${spaceId}/unbind`, { session });
export const setMemberRole = (spaceId, user, role) => post(`spaces/${spaceId}/members/role`, { ...ref(user), role });
export const resetInvite = (spaceId) => post(`spaces/${spaceId}/invite/reset`, {});
export const setShare = (spaceId, action) => post(`spaces/${spaceId}/share`, { action });
export const listTasks = (spaceId, includeArchived = false) =>
  get(`spaces/${spaceId}/tasks`, includeArchived ? { include_archived: 1 } : {});
export const getTask = (spaceId, seq) => get(`spaces/${spaceId}/tasks/${seq}`);
export const deleteTask = (spaceId, seq) => post(`spaces/${spaceId}/tasks/${seq}/delete`, {});
export const listEvents = (spaceId, limit = 50) => get(`spaces/${spaceId}/events`, { limit });
export const getPublicBoard = (token) => get(`b/${encodeURIComponent(token)}/api/board`);

/** Assignees are sent as identity refs only; the server resolves nicknames. */
const withRefs = (fields) => ("assignees" in fields ? { ...fields, assignees: fields.assignees.map(ref) } : fields);
export const createTask = (spaceId, fields) => post(`spaces/${spaceId}/tasks`, withRefs(fields));
export const updateTask = (spaceId, seq, patch) => post(`spaces/${spaceId}/tasks/${seq}`, withRefs(patch));

/** Best-effort user-facing message from a rejected call. */
export const errorText = (err) => (err && typeof err.message === "string" && err.message ? err.message : "请求失败");
