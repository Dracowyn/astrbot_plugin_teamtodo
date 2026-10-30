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

export const isSameDraft = (a, b) => JSON.stringify(a) === JSON.stringify(b);
