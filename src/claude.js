const Anthropic = require("@anthropic-ai/sdk");

const MODEL = process.env.ANTHROPIC_MODEL || "claude-sonnet-5";

let client = null;
function getClient() {
  if (!client) {
    if (!process.env.ANTHROPIC_API_KEY) {
      throw new Error(
        "ANTHROPIC_API_KEY is not set. Add it to your environment or .env file before starting a session."
      );
    }
    client = new Anthropic({ apiKey: process.env.ANTHROPIC_API_KEY });
  }
  return client;
}

async function sendMessage(system, messages, maxTokens = 1024) {
  const anthropic = getClient();
  const response = await anthropic.messages.create({
    model: MODEL,
    max_tokens: maxTokens,
    system,
    messages,
  });
  return response.content.map((block) => (block.type === "text" ? block.text : "")).join("");
}

// The session prompt is instructed to append a trailing ```json ... ``` block
// only on the turn that closes the session. Split it off so the user never
// sees it and the app can persist it as structured data.
function splitExtraction(text) {
  const fenceMatch = text.match(/```json\s*([\s\S]*?)```\s*$/);
  if (!fenceMatch) {
    return { visible: text.trim(), extraction: null };
  }
  const visible = text.slice(0, fenceMatch.index).trim();
  let extraction = null;
  try {
    extraction = JSON.parse(fenceMatch[1]);
  } catch (err) {
    extraction = null;
  }
  return { visible, extraction };
}

module.exports = { sendMessage, splitExtraction, MODEL };
