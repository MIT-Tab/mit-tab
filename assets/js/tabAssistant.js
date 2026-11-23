const launcher = document.getElementById("tab-gpt-launcher");
const panel = document.getElementById("tab-gpt-panel");
const form = document.getElementById("assistant-form");
const transcript = panel.querySelector(".tab-gpt-transcript");
const conversation = document.getElementById("assistant-conversation");
const question = document.getElementById("assistant-question");
const status = document.getElementById("assistant-status");
const send = form.querySelector(".tab-gpt-send");
const buttons = form.querySelectorAll("button");
const history = [];

function scrollToLatest() {
  transcript.scrollTop = transcript.scrollHeight;
}

launcher.addEventListener("click", () => {
  if (panel.open) {
    panel.close();
    return;
  }
  panel.show();
  launcher.setAttribute("aria-expanded", "true");
  if (window.matchMedia("(pointer: fine)").matches) question.focus();
  scrollToLatest();
});

panel.querySelector(".tab-gpt-close").addEventListener("click", () => {
  panel.close();
});

panel.addEventListener("close", () => {
  launcher.setAttribute("aria-expanded", "false");
  if (
    panel.contains(document.activeElement) ||
    document.activeElement === document.body
  ) {
    launcher.focus();
  }
});

panel.addEventListener("keydown", (event) => {
  if (event.key === "Escape") {
    event.stopPropagation();
    panel.close();
  }
});

question.addEventListener("keydown", (event) => {
  if (event.key === "Enter" && !event.shiftKey && !event.isComposing) {
    event.preventDefault();
    form.requestSubmit(send);
  }
});

function addSources(message, items) {
  if (!items.length) return;
  const sources = document.createElement("details");
  sources.className = "tab-gpt-sources";
  const heading = document.createElement("summary");
  heading.textContent = `Sources (${items.length})`;
  sources.appendChild(heading);
  items.forEach((item) => {
    const details = document.createElement("details");
    const summary = document.createElement("summary");
    summary.textContent = `${item.path}:${item.line}`;
    const excerpt = document.createElement("pre");
    excerpt.textContent = item.text;
    details.append(summary, excerpt);
    if (item.url) {
      const link = document.createElement("a");
      link.href = item.url;
      link.textContent = "Open on GitHub";
      details.appendChild(link);
    }
    sources.appendChild(details);
  });
  message.appendChild(sources);
}

function addMessage(text, isUser = false, sources = []) {
  const message = document.createElement("div");
  message.className = isUser
    ? "tab-gpt-message tab-gpt-message--user"
    : "tab-gpt-message";
  message.setAttribute("aria-label", isUser ? "You" : "tab GPT");
  message.textContent = text;
  addSources(message, sources);
  conversation.appendChild(message);
  scrollToLatest();
}

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  if (send.disabled) return;
  const text = question.value.trim();
  if (text.length < 2) {
    status.textContent = "Enter at least two characters.";
    return;
  }
  const mode = event.submitter?.value || send.value;
  const data = new FormData(form);
  data.set("question", text);
  data.set("mode", mode);
  if (mode === "ask") data.set("history", history.join("\n").slice(-2400));
  addMessage(text, true);
  question.value = "";
  buttons.forEach((button) => button.setAttribute("disabled", ""));
  status.textContent = mode === "ask" ? "Looking that up…" : "Searching…";
  scrollToLatest();
  try {
    const response = await fetch(form.action, { method: "POST", body: data });
    const result = await response.json();
    const sources = result.sources || [];
    const message =
      result.message ||
      (sources.length ? "Code and docs" : "No matching source found.");
    addMessage(result.answer || message, false, sources);
    if (result.answer) {
      history.push(`Question: ${text}\nAnswer: ${result.answer}`);
      if (history.length > 3) history.shift();
    }
  } catch {
    addMessage("Could not load help. Try again.");
  } finally {
    status.textContent = "";
    buttons.forEach((button) => button.removeAttribute("disabled"));
  }
});
