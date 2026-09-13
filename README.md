# story-journal

A journaling partner that reads each entry against a simple story framework —
want, conflict, decision, posture — and asks one question at a time to find
what's missing. Built from the spec in the project brief.

## How it works

- The user writes a raw entry in a big, template-free text box.
- The server sends it to Claude with a system prompt that runs the framework
  (`src/prompts.js`), along with silent context (`src/context.js`): the last
  three entries' want/conflict/decision, all open threads, any thread that's
  recurred three or more times, and the onboarding answers.
- Claude asks up to four questions, one at a time, and closes by asking for
  one concrete thing to do before the next session.
- On the closing turn only, Claude appends a fenced ` ```json ` data block.
  The server strips it before showing anything to the user
  (`src/claude.js#splitExtraction`) and persists it as structured data,
  separate from the raw entry text, so extraction can be re-run later without
  losing the original writing.
- A first-time user gets a fixed four-question onboarding instead of an entry
  prompt (life sentence, ten-year want, known conflict, what's been tried).
- "Story Review" reads the last ~5 weeks of structured data and asks Claude
  one question: what is this chapter about (conflict mix, posture drift,
  longest-open threads, wants vs. decisions).

## Data

Stored in `data/db.json` (gitignored): the raw entry text and the structured
extraction are kept as separate fields per entry, per the brief's schema
note, so the raw writing is never lost even if extraction is redone later.

## Run it

```bash
npm install
cp .env.example .env   # add your ANTHROPIC_API_KEY
npm start
```

Then open http://localhost:3000.
