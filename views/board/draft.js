// Editable form state for a task (inputs speak strings; the API speaks Unix seconds and identity refs).
import { fromInputParts, toInputParts, userKey } from "./format.js";

export function emptyDraft() {
  return { title: "", status: "todo", description: "", assigneeKeys: [], dueDate: "", dueTime: "", priority: "", tags: [] };
}

export function draftFromTask(task) {
  const { date, time } = toInputParts(task.due_at);
  return {
    title: task.title,
    status: task.status,
    description: task.description,
    assigneeKeys: task.assignees.map(userKey),
    dueDate: date,
    dueTime: time,
    priority: task.priority ?? "",
    tags: [...task.tags],
  };
}

/** Converts a draft to the API field set; `members` resolves selected keys back to identities. */
export function fieldsFromDraft(draft, members) {
  return {
    title: draft.title.trim(),
    status: draft.status,
    description: draft.description,
    assignees: members.filter((m) => draft.assigneeKeys.includes(userKey(m))),
    due_at: fromInputParts(draft.dueDate, draft.dueTime),
    priority: draft.priority || null,
    tags: draft.tags,
  };
}

/**
 * Patch for POST tasks/<seq>: only fields that differ from `original`. Cleared due date / priority become null
 * (the server clears them); assignees and tags are sent as whole lists (the server replaces them).
 */
export function patchFromDraft(draft, original, members) {
  const next = fieldsFromDraft(draft, members);
  const before = fieldsFromDraft(original, members);
  const patch = {};
  for (const key of Object.keys(next)) {
    if (JSON.stringify(next[key]) !== JSON.stringify(before[key])) patch[key] = next[key];
  }
  // Selection keys are what the user edits; compare them directly so non-member assignees do not hide changes.
  if (JSON.stringify(draft.assigneeKeys) === JSON.stringify(original.assigneeKeys)) delete patch.assignees;
  else patch.assignees = next.assignees;
  return patch;
}

export const isSameDraft = (a, b) => JSON.stringify(a) === JSON.stringify(b);
