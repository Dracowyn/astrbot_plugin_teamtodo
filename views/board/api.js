// Data access for the admin page (and, until M4, the public page). Every function is async and returns
// plain JSON-shaped objects. The HTTP contract, data shapes and error rules are fixed in docs/design.md
// section 7 ("M3 定稿"); this file only maps them to functions.
//
// Transport (`send`):
//   - inside AstrBot: window.AstrBotPluginView.apiGet(endpoint, params) / apiPost(endpoint, body);
//     endpoints have no leading slash and no query string. GET params must stay scalars (the bridge
//     serializes arrays as `key[]=a`).
//   - standalone (python -m http.server demo): the in-memory mock in mock.js, same endpoints and shapes.
//
// Failures reject with an Error whose `message` is user-facing Chinese text. A rejected bridge call may
// carry only the message and no HTTP status, so callers must show the message and never branch on `status`.
//
// The public page's `getPublicBoard` is the M4 endpoint (`b/<token>/api/board`) and always uses the mock for now.

import { request } from "./mock.js";

const bridge = () => window.AstrBotPluginView;

/** Plugin web API responses may arrive wrapped as {status, data}; unwrap defensively, pass plain data through. */
const unwrap = (res) => (res && typeof res === "object" && res.status === "ok" && "data" in res ? res.data : res);

async function send(method, endpoint, payload) {
  const view = bridge();
  if (!view) return request(method, endpoint, payload);
  const result = method === "GET" ? await view.apiGet(endpoint, payload ?? {}) : await view.apiPost(endpoint, payload ?? {});
  return unwrap(result);
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
export const syncMembers = (spaceId) => post(`spaces/${spaceId}/members/sync`, {});
export const resetInvite = (spaceId) => post(`spaces/${spaceId}/invite/reset`, {});
export const listTasks = (spaceId, includeArchived = false) =>
  get(`spaces/${spaceId}/tasks`, includeArchived ? { include_archived: 1 } : {});
export const getTask = (spaceId, seq) => get(`spaces/${spaceId}/tasks/${seq}`);
export const deleteTask = (spaceId, seq) => post(`spaces/${spaceId}/tasks/${seq}/delete`, {});
export const listEvents = (spaceId, limit = 50) => get(`spaces/${spaceId}/events`, { limit });
/** M4 endpoint; mock-only until the public server exists. */
export const getPublicBoard = (token) => request("GET", `b/${encodeURIComponent(token)}/api/board`, {});

/** Assignees are sent as identity refs only; the server resolves nicknames. */
const withRefs = (fields) => ("assignees" in fields ? { ...fields, assignees: fields.assignees.map(ref) } : fields);
export const createTask = (spaceId, fields) => post(`spaces/${spaceId}/tasks`, withRefs(fields));
export const updateTask = (spaceId, seq, patch) => post(`spaces/${spaceId}/tasks/${seq}`, withRefs(patch));

/** Best-effort user-facing message from a rejected call. */
export function errorText(err) {
  if (typeof err === "string" && err) return err;
  return err && typeof err.message === "string" && err.message ? err.message : "请求失败";
}
