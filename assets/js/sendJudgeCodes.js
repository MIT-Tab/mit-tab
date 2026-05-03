document.addEventListener("DOMContentLoaded", () => {
  const table = document.getElementById("judge-email-table");
  if (!table) {
    return;
  }
  const selectNeverReceivedButton = document.getElementById(
    "select-never-received",
  );
  const selectAllButton = document.getElementById("select-all-judges");

  const headers = table.querySelectorAll("[data-sort-key]");
  const checkboxRows = () =>
    Array.from(table.querySelectorAll("tbody tr[data-can-send='true']"));

  const getValue = (cell) =>
    cell?.dataset.sortValue ?? cell?.textContent?.trim().toLowerCase() ?? "";

  const setSelection = (predicate) => {
    checkboxRows().forEach((row) => {
      const checkbox = row.querySelector("input[name='judge_ids']");
      if (!checkbox) {
        return;
      }

      checkbox.checked = predicate(row);
    });
  };

  const setSortDirection = (cell, direction) => {
    const headerCell = cell;
    headerCell.dataset.sortDir = direction;
    const arrow = headerCell.querySelector(".sort-arrow");
    let arrowText = "";
    if (direction === "asc") {
      arrowText = "▲";
    } else if (direction === "desc") {
      arrowText = "▼";
    }
    if (arrow) {
      arrow.textContent = arrowText;
    }
  };

  const sortTable = (header) => {
    const key = header.dataset.sortKey;
    if (!key) return;

    const tbody = table.querySelector("tbody");
    if (!tbody) return;

    const rows = Array.from(tbody.rows);
    const currentDir = header.dataset.sortDir === "asc" ? "desc" : "asc";

    headers.forEach((th) => {
      if (th === header) return;
      setSortDirection(th, "");
    });
    setSortDirection(header, currentDir);

    rows.sort((a, b) => {
      const aCell = a.cells[header.cellIndex];
      const bCell = b.cells[header.cellIndex];
      let aVal = getValue(aCell);
      let bVal = getValue(bCell);

      if (key === "sent") {
        aVal = parseInt(aCell?.dataset.sortValue || "0", 10);
        bVal = parseInt(bCell?.dataset.sortValue || "0", 10);
      }

      if (aVal < bVal) return currentDir === "asc" ? -1 : 1;
      if (aVal > bVal) return currentDir === "asc" ? 1 : -1;
      return 0;
    });

    rows.forEach((row) => tbody.appendChild(row));
  };

  headers.forEach((th) => {
    const header = th;
    header.style.cursor = "pointer";
    header.addEventListener("click", (event) => {
      event.preventDefault();
      sortTable(header);
    });
  });

  if (selectNeverReceivedButton) {
    selectNeverReceivedButton.addEventListener("click", () => {
      setSelection((row) => row.dataset.neverReceived === "true");
    });
  }

  if (selectAllButton) {
    selectAllButton.addEventListener("click", () => {
      setSelection(() => true);
    });
  }
});
