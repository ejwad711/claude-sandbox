const fs = require("fs");
const path = require("path");
const crypto = require("crypto");

const DB_PATH = path.join(__dirname, "..", "data", "db.json");

function load() {
  if (!fs.existsSync(DB_PATH)) {
    return { onboarding: null, entries: [], threads: [] };
  }
  return JSON.parse(fs.readFileSync(DB_PATH, "utf8"));
}

function save(data) {
  fs.mkdirSync(path.dirname(DB_PATH), { recursive: true });
  fs.writeFileSync(DB_PATH, JSON.stringify(data, null, 2));
}

function getOnboarding() {
  return load().onboarding;
}

function saveOnboarding(answers) {
  const data = load();
  data.onboarding = { ...answers, created_at: new Date().toISOString() };
  save(data);
  return data.onboarding;
}

function createEntry(rawText, date) {
  const data = load();
  const entry = {
    id: crypto.randomUUID(),
    date: date || new Date().toISOString().slice(0, 10),
    raw_text: rawText,
    status: "active",
    messages: [{ role: "user", content: rawText }],
    extraction: null,
    created_at: new Date().toISOString(),
  };
  data.entries.push(entry);
  save(data);
  return entry;
}

function getEntry(id) {
  const data = load();
  return data.entries.find((e) => e.id === id) || null;
}

function deleteEntry(id) {
  const data = load();
  data.entries = data.entries.filter((e) => e.id !== id);
  save(data);
}

function appendMessage(id, role, content) {
  const data = load();
  const entry = data.entries.find((e) => e.id === id);
  if (!entry) throw new Error("entry not found");
  entry.messages.push({ role, content });
  save(data);
  return entry;
}

function getActiveEntry() {
  const data = load();
  return data.entries.find((e) => e.status === "active") || null;
}

function getRecentClosedEntries(limit) {
  const data = load();
  return data.entries
    .filter((e) => e.status === "closed" && e.extraction)
    .sort((a, b) => new Date(b.date) - new Date(a.date))
    .slice(0, limit);
}

function getEntriesSince(sinceDate) {
  const data = load();
  return data.entries
    .filter((e) => e.status === "closed" && e.extraction && e.date >= sinceDate)
    .sort((a, b) => new Date(a.date) - new Date(b.date));
}

function getOpenThreads() {
  const data = load();
  return data.threads.filter((t) => t.status === "open");
}

function getRecurringThreads(minCount) {
  return getOpenThreads().filter((t) => t.occurrence_count >= minCount);
}

function getLastOpenThread() {
  const open = getOpenThreads();
  if (open.length === 0) return null;
  return open.sort((a, b) => new Date(b.updated_at) - new Date(a.updated_at))[0];
}

function upsertThreadsFromExtraction(entryId, extraction) {
  const data = load();
  const now = new Date().toISOString();

  for (const t of extraction.open_threads || []) {
    let thread = data.threads.find((x) => x.slug === t.id);
    if (thread) {
      thread.description = t.description || thread.description;
      thread.next_action = t.next_action || thread.next_action;
      thread.due = t.due || thread.due;
      thread.status = "open";
      thread.occurrence_count += 1;
      thread.last_entry_id = entryId;
      thread.updated_at = now;
    } else {
      data.threads.push({
        slug: t.id,
        description: t.description || "",
        next_action: t.next_action || "",
        due: t.due || null,
        status: "open",
        occurrence_count: 1,
        first_entry_id: entryId,
        last_entry_id: entryId,
        updated_at: now,
      });
    }
  }

  for (const slug of extraction.closed_threads || []) {
    const thread = data.threads.find((x) => x.slug === slug);
    if (thread) {
      thread.status = "closed";
      thread.closed_at = now;
      thread.updated_at = now;
    }
  }

  save(data);
}

function closeEntry(id, extraction) {
  const data = load();
  const entry = data.entries.find((e) => e.id === id);
  if (!entry) throw new Error("entry not found");
  entry.status = "closed";
  entry.extraction = extraction;
  save(data);
  upsertThreadsFromExtraction(id, extraction);
  return entry;
}

module.exports = {
  getOnboarding,
  saveOnboarding,
  createEntry,
  getEntry,
  deleteEntry,
  appendMessage,
  getActiveEntry,
  getRecentClosedEntries,
  getEntriesSince,
  getOpenThreads,
  getRecurringThreads,
  getLastOpenThread,
  closeEntry,
};
