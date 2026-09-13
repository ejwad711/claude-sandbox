const db = require("./db");

function summarizeEntry(entry) {
  const x = entry.extraction;
  return `- ${entry.date}: want="${x.want}" | conflict(${x.conflict.type})="${x.conflict.description}" | decision="${x.decision}" | posture=${x.posture}`;
}

function buildSessionContext() {
  const onboarding = db.getOnboarding();
  const recent = db.getRecentClosedEntries(3);
  const openThreads = db.getOpenThreads();
  const recurring = db.getRecurringThreads(3);

  const parts = [];

  if (onboarding) {
    parts.push(
      "ONBOARDING ANSWERS:\n" +
        `- Funeral sentence: ${onboarding.funeral_sentence}\n` +
        `- Ten-year want: ${onboarding.ten_year_want}\n` +
        `- Known conflict: ${onboarding.known_conflict}\n` +
        `- Tried before: ${onboarding.tried_before}`
    );
  }

  if (recent.length > 0) {
    parts.push("LAST 3 ENTRIES:\n" + recent.map(summarizeEntry).join("\n"));
  }

  if (openThreads.length > 0) {
    parts.push(
      "OPEN THREADS:\n" +
        openThreads
          .map(
            (t) =>
              `- [${t.slug}] ${t.description} (next action: ${t.next_action || "none"}${
                t.due ? ", due " + t.due : ""
              }, seen ${t.occurrence_count}x)`
          )
          .join("\n")
    );
  }

  if (recurring.length > 0) {
    parts.push(
      "RECURRING 3+ TIMES:\n" +
        recurring.map((t) => `- [${t.slug}] ${t.description} (${t.occurrence_count}x)`).join("\n")
    );
  }

  if (parts.length === 0) return null;
  return parts.join("\n\n");
}

module.exports = { buildSessionContext };
