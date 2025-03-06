document.addEventListener("DOMContentLoaded", function initBackupList() {
  const roundFilter = document.getElementById("round-filter");
  const rows = document.querySelectorAll("#backup-table tbody tr");

  if (!roundFilter) return;

  const roundSet = new Set();
  let hasOther = false;

  rows.forEach(row => {
    const roundValue = row.getAttribute("data-round");

    if (/^\d+$/.test(roundValue)) {
      roundSet.add(parseInt(roundValue, 10));
    } else {
      hasOther = true;
    }
  });

  roundFilter.innerHTML = `<option value="">All</option>`;

  [...roundSet]
    .sort((a, b) => a - b)
    .forEach(num => {
      roundFilter.innerHTML += `<option value="${num}">${num}</option>`;
    });

  if (hasOther) {
    roundFilter.innerHTML += `<option value="Other">Other</option>`;
  }

  roundFilter.addEventListener("change", function handleFilterChange() {
    const selectedRound = roundFilter.value;

    rows.forEach(row => {
      const tableRow = row;
      const rowRound = tableRow.getAttribute("data-round");

      const shouldShow =
        !selectedRound ||
        rowRound === selectedRound ||
        (selectedRound === "Other" && !/^\d+$/.test(rowRound));

      tableRow.style.display = shouldShow ? "" : "none";
    });
  });
});
