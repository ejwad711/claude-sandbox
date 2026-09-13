require("dotenv").config();
const express = require("express");
const path = require("path");

const db = require("./src/db");
const { buildSessionContext } = require("./src/context");
const { sendMessage, splitExtraction } = require("./src/claude");
const { SESSION_SYSTEM_PROMPT, REVIEW_SYSTEM_PROMPT } = require("./src/prompts");

const app = express();
app.use(express.json());
app.use(express.static(path.join(__dirname, "public")));

// Claude Pro (claude.ai) does not include API access — AI features need a
// separately billed ANTHROPIC_API_KEY. Without one, the app still works as
// a plain journal: entries save, nothing calls Claude.
const AI_ENABLED = !!process.env.ANTHROPIC_API_KEY;

function buildSystemPrompt(isFirstSession) {
  let system = SESSION_SYSTEM_PROMPT;
  if (isFirstSession) {
    system += "\n\nThis is the user's first session. There is no prior entry — run the onboarding conversation.";
    return system;
  }
  const context = buildSessionContext();
  if (context) {
    system += "\n\n---\nCONTEXT (not visible to the user; use it silently for continuity)\n\n" + context;
  }
  return system;
}

// GET /api/state - what should the frontend show right now
app.get("/api/state", (req, res) => {
  const onboarding = db.getOnboarding();
  const activeEntry = db.getActiveEntry();
  const lastOpenThread = db.getLastOpenThread();
  res.json({
    aiEnabled: AI_ENABLED,
    onboardingComplete: !!onboarding,
    activeEntry: activeEntry
      ? { id: activeEntry.id, messages: activeEntry.messages, status: activeEntry.status }
      : null,
    lastOpenThread: lastOpenThread || null,
  });
});

// POST /api/onboarding - store the four fixed onboarding answers
app.post("/api/onboarding", (req, res) => {
  const { funeral_sentence, ten_year_want, known_conflict, tried_before } = req.body || {};
  if (!funeral_sentence || !ten_year_want || !known_conflict || !tried_before) {
    return res.status(400).json({ error: "all four onboarding answers are required" });
  }
  const onboarding = db.saveOnboarding({ funeral_sentence, ten_year_want, known_conflict, tried_before });
  res.json({ onboarding });
});

// POST /api/entries - start a session from a new journal entry
app.post("/api/entries", async (req, res) => {
  try {
    const { raw_text } = req.body || {};
    if (!raw_text || !raw_text.trim()) {
      return res.status(400).json({ error: "raw_text is required" });
    }
    if (db.getActiveEntry()) {
      return res.status(409).json({ error: "a session is already in progress" });
    }

    if (!AI_ENABLED) {
      const entry = db.createEntry(raw_text);
      db.closeEntry(entry.id, null);
      return res.json({ entryId: entry.id, message: "Saved.", done: true, aiEnabled: false });
    }

    const entry = db.createEntry(raw_text);
    try {
      const isFirstSession = db.getRecentClosedEntries(1).length === 0 && !db.getOnboarding();
      const system = buildSystemPrompt(isFirstSession);

      const reply = await sendMessage(system, entry.messages);
      const { visible, extraction } = splitExtraction(reply);

      db.appendMessage(entry.id, "assistant", reply);

      let done = false;
      if (extraction) {
        db.closeEntry(entry.id, extraction);
        done = true;
      }

      res.json({ entryId: entry.id, message: visible, done });
    } catch (err) {
      db.deleteEntry(entry.id);
      throw err;
    }
  } catch (err) {
    res.status(500).json({ error: err.message });
  }
});

// POST /api/entries/:id/reply - continue an in-progress session
app.post("/api/entries/:id/reply", async (req, res) => {
  try {
    if (!AI_ENABLED) {
      return res.status(400).json({ error: "AI features are off — add ANTHROPIC_API_KEY to enable follow-up questions" });
    }
    const { text } = req.body || {};
    if (!text || !text.trim()) {
      return res.status(400).json({ error: "text is required" });
    }
    const entry = db.getEntry(req.params.id);
    if (!entry) return res.status(404).json({ error: "entry not found" });
    if (entry.status !== "active") return res.status(409).json({ error: "session already closed" });

    db.appendMessage(entry.id, "user", text);
    const updated = db.getEntry(entry.id);

    const isFirstSession = db.getRecentClosedEntries(1).length === 0 && !db.getOnboarding();
    const system = buildSystemPrompt(isFirstSession);

    const reply = await sendMessage(system, updated.messages);
    const { visible, extraction } = splitExtraction(reply);

    db.appendMessage(entry.id, "assistant", reply);

    let done = false;
    if (extraction) {
      db.closeEntry(entry.id, extraction);
      done = true;
    }

    res.json({ message: visible, done });
  } catch (err) {
    res.status(500).json({ error: err.message });
  }
});

// GET /api/review - monthly story review over the last ~5 weeks
app.get("/api/review", async (req, res) => {
  try {
    if (!AI_ENABLED) {
      return res.json({
        text: "Story Review needs an Anthropic API key. Add ANTHROPIC_API_KEY to your .env to turn on AI features.",
        aiEnabled: false,
      });
    }
    const since = new Date();
    since.setDate(since.getDate() - 35);
    const sinceDate = since.toISOString().slice(0, 10);
    const entries = db.getEntriesSince(sinceDate);

    if (entries.length === 0) {
      return res.json({ text: "No closed sessions in the last five weeks yet — nothing to review." });
    }

    const onboarding = db.getOnboarding();
    const payload = entries.map((e) => ({ date: e.date, ...e.extraction }));

    let system = REVIEW_SYSTEM_PROMPT;
    if (onboarding) {
      system += "\n\nONBOARDING ANSWERS:\n" + JSON.stringify(onboarding, null, 2);
    }

    const userMessage = `Session data for the last ${entries.length} entries (${sinceDate} to today):\n\n${JSON.stringify(
      payload,
      null,
      2
    )}`;

    const reply = await sendMessage(system, [{ role: "user", content: userMessage }], 1200);
    res.json({ text: reply.trim() });
  } catch (err) {
    res.status(500).json({ error: err.message });
  }
});

const PORT = process.env.PORT || 3000;
app.listen(PORT, () => {
  console.log(`story-journal listening on http://localhost:${PORT}`);
});
