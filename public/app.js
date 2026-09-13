const app = document.getElementById("app");
const navJournal = document.getElementById("nav-journal");
const navReview = document.getElementById("nav-review");

const ONBOARDING_QUESTIONS = [
  { key: "funeral_sentence", text: "What do you want your life to be about — as a sentence you'd be willing to have read at your funeral?" },
  { key: "ten_year_want", text: "What do you want in the next ten years? Be concrete." },
  { key: "known_conflict", text: "What's the one conflict you already know is standing in the way?" },
  { key: "tried_before", text: "What have you tried before, and why did it stop?" },
];

let state = null;

async function api(path, opts) {
  const res = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...opts,
  });
  const data = await res.json();
  if (!res.ok) throw new Error(data.error || "request failed");
  return data;
}

function el(tag, attrs = {}, children = []) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k === "text") node.textContent = v;
    else node.setAttribute(k, v);
  }
  for (const c of [].concat(children)) node.appendChild(c);
  return node;
}

async function loadState() {
  state = await api("/api/state");
}

function renderJournal() {
  app.innerHTML = "";

  if (!state.onboardingComplete) {
    return renderOnboarding();
  }

  if (state.activeEntry) {
    return renderChat(state.activeEntry);
  }

  return renderNewEntry();
}

function renderOnboarding(step = 0, answers = {}) {
  app.innerHTML = "";
  if (step >= ONBOARDING_QUESTIONS.length) {
    api("/api/onboarding", { method: "POST", body: JSON.stringify(answers) })
      .then(async () => {
        await loadState();
        renderJournal();
      })
      .catch((err) => app.appendChild(el("div", { class: "error", text: err.message })));
    app.appendChild(el("p", { text: "Saving…" }));
    return;
  }

  const q = ONBOARDING_QUESTIONS[step];
  const wrap = el("div");
  wrap.appendChild(el("p", { class: "onboard-q", text: q.text }));
  const input = el("textarea", { rows: "4", style: "min-height:100px" });
  wrap.appendChild(input);
  const btn = el("button", { class: "primary", text: step === ONBOARDING_QUESTIONS.length - 1 ? "Finish" : "Next" });
  btn.addEventListener("click", () => {
    const val = input.value.trim();
    if (!val) return;
    answers[q.key] = val;
    renderOnboarding(step + 1, answers);
  });
  wrap.appendChild(btn);
  app.appendChild(wrap);
  input.focus();
}

function renderNewEntry() {
  const wrap = el("div");

  if (state.lastOpenThread) {
    const t = state.lastOpenThread;
    const label = t.next_action ? `${t.description} — ${t.next_action}` : t.description;
    wrap.appendChild(el("div", { class: "thread-prompt", text: label }));
  }

  const textarea = el("textarea", { placeholder: "" });
  wrap.appendChild(textarea);

  const err = el("div", { class: "error" });
  const btn = el("button", { class: "primary", text: "Submit" });
  btn.addEventListener("click", async () => {
    const text = textarea.value.trim();
    if (!text) return;
    btn.disabled = true;
    btn.textContent = "…";
    err.textContent = "";
    try {
      const res = await api("/api/entries", { method: "POST", body: JSON.stringify({ raw_text: text }) });
      await loadState();
      renderChat({ id: res.entryId, messages: [{ role: "user", content: text }, { role: "assistant", content: res.message }], status: res.done ? "closed" : "active" }, res.done);
    } catch (e) {
      err.textContent = e.message;
      btn.disabled = false;
      btn.textContent = "Submit";
    }
  });

  wrap.appendChild(btn);
  wrap.appendChild(err);
  app.appendChild(wrap);
  textarea.focus();
}

function renderChat(entry, justClosed = false) {
  app.innerHTML = "";
  const chat = el("div", { class: "chat" });

  for (const m of entry.messages) {
    if (m.role === "user" && chat.children.length === 0) {
      // the raw entry itself — show as the opening user message
    }
    const bubble = el("div", { class: `msg ${m.role}` });
    bubble.appendChild(el("p", { text: m.content }));
    chat.appendChild(bubble);
  }
  app.appendChild(chat);

  if (entry.status === "closed" || justClosed) {
    app.appendChild(el("div", { class: "done-note", text: "Session closed. Come back tomorrow." }));
    const btn = el("button", { class: "primary", text: "New entry" });
    btn.addEventListener("click", async () => {
      await loadState();
      renderNewEntry();
    });
    app.appendChild(btn);
    return;
  }

  const row = el("div", { class: "reply-row" });
  const input = el("input", { type: "text", placeholder: "Reply…" });
  const err = el("div", { class: "error" });
  const btn = el("button", { class: "primary", text: "Send" });

  async function send() {
    const text = input.value.trim();
    if (!text) return;
    btn.disabled = true;
    input.disabled = true;
    const userBubble = el("div", { class: "msg user" });
    userBubble.appendChild(el("p", { text }));
    chat.appendChild(userBubble);
    input.value = "";
    err.textContent = "";
    try {
      const res = await api(`/api/entries/${entry.id}/reply`, { method: "POST", body: JSON.stringify({ text }) });
      const assistantBubble = el("div", { class: "msg assistant" });
      assistantBubble.appendChild(el("p", { text: res.message }));
      chat.appendChild(assistantBubble);
      entry.messages.push({ role: "user", content: text }, { role: "assistant", content: res.message });
      if (res.done) {
        row.remove();
        app.appendChild(el("div", { class: "done-note", text: "Session closed. Come back tomorrow." }));
        const newBtn = el("button", { class: "primary", text: "New entry" });
        newBtn.addEventListener("click", async () => {
          await loadState();
          renderNewEntry();
        });
        app.appendChild(newBtn);
      } else {
        btn.disabled = false;
        input.disabled = false;
        input.focus();
      }
    } catch (e) {
      err.textContent = e.message;
      btn.disabled = false;
      input.disabled = false;
    }
  }

  btn.addEventListener("click", send);
  input.addEventListener("keydown", (e) => {
    if (e.key === "Enter") send();
  });

  row.appendChild(input);
  row.appendChild(btn);
  app.appendChild(row);
  app.appendChild(err);
  input.focus();
}

async function renderReview() {
  app.innerHTML = "";
  app.appendChild(el("p", { text: "Reading the last five weeks…" }));
  try {
    const res = await api("/api/review");
    app.innerHTML = "";
    app.appendChild(el("div", { class: "review-text", text: res.text }));
  } catch (e) {
    app.innerHTML = "";
    app.appendChild(el("div", { class: "error", text: e.message }));
  }
}

navJournal.addEventListener("click", () => {
  navJournal.classList.add("active");
  navReview.classList.remove("active");
  loadState().then(renderJournal);
});

navReview.addEventListener("click", () => {
  navReview.classList.add("active");
  navJournal.classList.remove("active");
  renderReview();
});

loadState().then(renderJournal);
