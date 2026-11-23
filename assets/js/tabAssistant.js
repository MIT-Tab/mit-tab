const form = document.getElementById("assistant-form");
const conversation = document.getElementById("assistant-conversation");
const question = document.getElementById("assistant-question");
const status = document.getElementById("assistant-status");
const sources = document.getElementById("assistant-sources");
const history = [];

function showSources(items) {
  sources.replaceChildren();
  if (!items.length) return;
  const heading = document.createElement("h4");
  heading.textContent = "Code and docs";
  sources.appendChild(heading);
  items.forEach((item) => {
    const details = document.createElement("details");
    details.className = "mb-2";
    const summary = document.createElement("summary");
    summary.textContent = `${item.path}:${item.line}`;
    const excerpt = document.createElement("pre");
    excerpt.className = "text-break";
    excerpt.style.whiteSpace = "pre-wrap";
    excerpt.textContent = item.text;
    details.append(summary, excerpt);
    if (item.url) {
      const link = document.createElement("a");
      link.href = item.url;
      link.textContent = "Open source on GitHub";
      details.appendChild(link);
    }
    sources.appendChild(details);
  });
}

function showTurn(label, text) {
  const row = document.createElement("div");
  row.className = "card card-body mb-2";
  const heading = document.createElement("strong");
  heading.textContent = label;
  const body = document.createElement("div");
  body.style.whiteSpace = "pre-wrap";
  body.textContent = text;
  row.append(heading, body);
  conversation.appendChild(row);
}

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  const mode = event.submitter?.value || "search";
  const data = new FormData(form);
  data.set("mode", mode);
  if (mode === "ask") {
    data.set("history", history.join("\n").slice(-2400));
    showTurn("You", question.value.trim());
  }
  const buttons = form.querySelectorAll("button");
  buttons.forEach((button) => {
    button.setAttribute("disabled", "");
  });
  status.textContent =
    mode === "ask" ? "Looking up your question…" : "Searching…";
  sources.replaceChildren();
  try {
    const response = await fetch(form.action, { method: "POST", body: data });
    const result = await response.json();
    status.textContent = result.message || "";
    if (result.answer) {
      showTurn("MIT-Tab", result.answer);
      history.push(
        `Question: ${data.get("question")}\nAnswer: ${result.answer}`,
      );
      if (history.length > 3) history.shift();
      question.value = "";
    }
    showSources(result.sources || []);
    if (!result.answer && !result.message && !result.sources?.length) {
      status.textContent = "No matching source found.";
    }
  } catch {
    status.textContent = "Could not load help. Try again.";
  } finally {
    buttons.forEach((button) => {
      button.removeAttribute("disabled");
    });
    question.focus();
  }
});
