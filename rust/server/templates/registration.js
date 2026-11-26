(() => {
  const form = document.getElementById("registration");
  if (!form) return;

  const privateEmails = form.dataset.privateEmailEnabled === "1";
  const directory = new WeakMap();

  async function loadDebaters(member) {
    const choice = member.querySelector("[data-choose-school]");
    const list = member.querySelector("[data-directory]");
    const wrapper = member.querySelector("[data-directory-wrapper]");
    const id = member.querySelector("[data-apda-id]").value;
    list.replaceChildren(new Option("Enter debater manually", ""));
    wrapper.hidden = true;
    if (!choice.value.startsWith("apda:")) return;

    const selection = choice.value;
    const school = selection.slice(5);
    try {
      const response = await fetch(`/registration/api/debaters/${encodeURIComponent(school)}/`);
      if (!response.ok || choice.value !== selection) return;
      const payload = await response.json();
      const entries = Array.isArray(payload) ? payload : payload.debaters;
      if (!Array.isArray(entries) || choice.value !== selection) return;
      const current = new Map();
      for (const debater of entries) {
        const apdaId = String(debater.apda_id ?? debater.id ?? "");
        if (!/^\d+$/.test(apdaId) || !debater.name) continue;
        current.set(apdaId, debater);
        list.append(new Option(debater.name, apdaId));
      }
      directory.set(member, current);
      if (current.has(id)) list.value = id;
      wrapper.hidden = current.size === 0;
    } catch (error) {
      // The name and email can still be entered manually.
      wrapper.hidden = true;
    }
  }

  async function chooseDebater(member, selected) {
    const choice = member.querySelector("[data-choose-school]");
    const list = member.querySelector("[data-directory]");
    const memberName = member.querySelector("[data-member-name]");
    const email = member.querySelector("[data-private-email]");
    const emailStatus = member.querySelector("[data-email-status]");
    member.querySelector("[data-apda-id]").value = selected;
    email.value = "";
    emailStatus.textContent = "";
    const match = directory.get(member)?.get(selected);
    member.querySelector("[data-qualified]").value = match?.apda_id ? "on" : "";
    if (!match) return;

    memberName.value = match.name;
    member.querySelector("[data-novice-status]").value =
      Number(match.novice_status) === 1 || String(match.status).toLowerCase() === "novice" ? "1" : "0";
    member.querySelector("[data-member-email]").value = "";
    if (!privateEmails) return;

    const school = choice.value;
    try {
      const response = await fetch("/registration/api/debater-email-status/", {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "X-CSRFToken": form.elements.csrfmiddlewaretoken.value,
        },
        body: JSON.stringify({ debater_id: selected, school_id: school.slice(5) }),
      });
      if (!response.ok || choice.value !== school || list.value !== selected) return;
      const result = await response.json();
      if (result.has_email && result.masked_email && choice.value === school && list.value === selected) {
        email.value = "on";
        emailStatus.textContent = `Using the email on file with APDA (${result.masked_email}).`;
      }
    } catch (error) {
      emailStatus.textContent = "";
    }
  }

  form.addEventListener("click", (event) => {
    const add = event.target.closest("[data-add]");
    if (add) {
      const kind = add.dataset.add;
      const container = document.getElementById(`registration-${kind}`);
      const template = document.getElementById(`registration-${kind}-template`);
      const count = form.elements[`${kind}-TOTAL_FORMS`];
      const index = Number(count.value);
      const limit = kind === "teams" ? Number(form.dataset.maxTeams) : 1000;
      const reusable = [...container.querySelectorAll("[data-form-row]")].find((row) => row.hidden);
      if (!reusable && index >= limit) return;
      const rowIndex = reusable?.querySelector("[data-delete-row]").name.split("-")[1] ?? index;
      const content = template.content.cloneNode(true);
      for (const field of content.querySelectorAll("[name]")) field.name = field.name.replace("__index__", rowIndex);
      const entry = content.querySelector("[data-form-row]");
      if (reusable) reusable.replaceWith(content);
      else {
        container.append(content);
        count.value = index + 1;
      }
      if (kind === "teams") {
        const school = form.elements.school.value;
        if (school && school !== "custom:-1") {
          for (const choice of entry.querySelectorAll("[data-choose-school]")) {
            choice.value = school;
            choice.dispatchEvent(new Event("change", { bubbles: true }));
          }
        }
      }
      entry.querySelector("input:not([type=hidden]), select")?.focus();
      return;
    }
    const remove = event.target.closest("[data-remove-row]");
    if (remove) {
      const row = remove.closest("[data-form-row]");
      row.querySelector("[data-delete-row]").value = "on";
      for (const field of row.querySelectorAll("input:not([data-delete-row]), select, button")) field.disabled = true;
      row.hidden = true;
    }
  });

  form.addEventListener("change", (event) => {
    const choice = event.target;
    const member = choice.closest("[data-member]");
    if (choice.matches("[data-choose-school]")) {
      const custom = choice.closest("label").nextElementSibling;
      custom.hidden = choice.value !== "custom:-1";
      if (member) {
        member.querySelector("[data-apda-id]").value = "";
        member.querySelector("[data-private-email]").value = "";
        member.querySelector("[data-qualified]").value = "";
        member.querySelector("[data-email-status]").textContent = "";
        loadDebaters(member);
      }
    }
    if (member && choice.matches("[data-directory]")) chooseDebater(member, choice.value);
  });

  for (const member of form.querySelectorAll("[data-member]")) loadDebaters(member);
})();
