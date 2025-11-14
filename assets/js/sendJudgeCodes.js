document.addEventListener("DOMContentLoaded", () => {
  const table = document.getElementById("judge-email-table");
  if (!table) {
    return;
  }

  const headers = table.querySelectorAll("[data-sort-key]");

  const getValue = (cell) =>
    cell?.dataset.sortValue ?? cell?.textContent?.trim().toLowerCase() ?? "";

  const sortTable = (header) => {
    const key = header.dataset.sortKey;
    if (!key) return;

    const tbody = table.querySelector("tbody");
    if (!tbody) return;

    const rows = Array.from(tbody.rows);
    const currentDir = header.dataset.sortDir === "asc" ? "desc" : "asc";

    headers.forEach((th) => {
      if (th === header) return;
      th.dataset.sortDir = "";
      const arrow = th.querySelector(".sort-arrow");
      if (arrow) {
        arrow.textContent = "";
      }
    });
    header.dataset.sortDir = currentDir;
    const activeArrow = header.querySelector(".sort-arrow");
    if (activeArrow) {
      activeArrow.textContent = currentDir === "asc" ? "▲" : "▼";
    }

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
    th.style.cursor = "pointer";
    th.addEventListener("click", (event) => {
      event.preventDefault();
      sortTable(th);
    });
  });
});
