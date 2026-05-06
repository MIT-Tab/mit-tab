document.addEventListener("DOMContentLoaded", () => {
  const tables = Array.from(
    document.querySelectorAll(".email-management-table"),
  );

  const getValue = (cell) =>
    cell?.dataset.sortValue ?? cell?.textContent?.trim().toLowerCase() ?? "";

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

  const sortTable = (table, header) => {
    const key = header.dataset.sortKey;
    if (!key) return;

    const tbody = table.querySelector("tbody");
    if (!tbody) return;

    const headers = table.querySelectorAll("[data-sort-key]");
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

      if (key === "sent" || key === "round" || key === "recipients") {
        aVal = parseInt(aCell?.dataset.sortValue || "0", 10);
        bVal = parseInt(bCell?.dataset.sortValue || "0", 10);
      }

      if (aVal < bVal) return currentDir === "asc" ? -1 : 1;
      if (aVal > bVal) return currentDir === "asc" ? 1 : -1;
      return 0;
    });

    rows.forEach((row) => tbody.appendChild(row));
  };

  tables.forEach((table) => {
    table.querySelectorAll("[data-sort-key]").forEach((th) => {
      const header = th;
      header.style.cursor = "pointer";
      header.addEventListener("click", (event) => {
        event.preventDefault();
        sortTable(table, header);
      });
    });
  });

  document.querySelectorAll("[data-select-target]").forEach((button) => {
    button.addEventListener("click", () => {
      const table = document.getElementById(button.dataset.selectTarget);
      if (!table) {
        return;
      }
      const mode = button.dataset.selectMode;
      const rows = Array.from(
        table.querySelectorAll("tbody tr[data-can-send='true']"),
      );
      rows.forEach((row) => {
        const checkbox = row.querySelector("input[type='checkbox']");
        if (!checkbox) {
          return;
        }
        checkbox.checked =
          mode === "all" || row.dataset.neverReceived === "true";
      });
    });
  });
});
