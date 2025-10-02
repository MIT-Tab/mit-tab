document.addEventListener("DOMContentLoaded", function initBackupList() {
  const typeFilter = document.getElementById("type-filter");
  const roundFilter = document.getElementById("round-filter");
  const scratchesFilter = document.getElementById("scratches-filter");
  const rows = document.querySelectorAll("#backup-table tbody tr.searchable");
  const table = document.getElementById("backup-table");

  if (!roundFilter) return;

  // Populate filter dropdowns
  const roundSet = new Set();
  const typeSet = new Set();
  let hasOtherRound = false;

  rows.forEach(row => {
    const roundValue = row.getAttribute("data-round");
    const typeValue = row.getAttribute("data-type");

    // Collect rounds
    if (/^\d+$/.test(roundValue)) {
      roundSet.add(parseInt(roundValue, 10));
    } else if (roundValue && roundValue !== "Unknown") {
      hasOtherRound = true;
    }

    // Collect types
    if (typeValue && typeValue !== "Unknown") {
      typeSet.add(typeValue);
    }
  });

  // Populate round filter
  [...roundSet]
    .sort((a, b) => a - b)
    .forEach(num => {
      roundFilter.innerHTML += `<option value="${num}">${num}</option>`;
    });

  if (hasOtherRound) {
    roundFilter.innerHTML += `<option value="Other">Other</option>`;
  }

  // Populate type filter
  [...typeSet]
    .sort()
    .forEach(type => {
      typeFilter.innerHTML += `<option value="${type}">${type}</option>`;
    });

  // Filter function
  function applyFilters() {
    const typeValue = typeFilter.value;
    const roundValue = roundFilter.value;
    const scratchesValue = scratchesFilter.value;

    rows.forEach(row => {
      const rowType = row.getAttribute("data-type");
      const rowRound = row.getAttribute("data-round");
      const rowScratches = row.getAttribute("data-scratches");

      const typeMatch = !typeValue || rowType === typeValue;
      const roundMatch = !roundValue || 
        rowRound === roundValue || 
        (roundValue === "Other" && !/^\d+$/.test(rowRound));
      const scratchesMatch = !scratchesValue || rowScratches === scratchesValue;

      row.style.display = (typeMatch && roundMatch && scratchesMatch) ? "" : "none";
    });
  }

  // Add filter event listeners
  typeFilter.addEventListener("change", applyFilters);
  roundFilter.addEventListener("change", applyFilters);
  scratchesFilter.addEventListener("change", applyFilters);

  // Sorting functionality
  let currentSort = { column: null, direction: null };

  const sortableHeaders = table.querySelectorAll("th.sortable");
  
  sortableHeaders.forEach(header => {
    header.addEventListener("click", function() {
      const column = this.getAttribute("data-column");
      const icon = this.querySelector(".sort-icon");
      
      // Reset other column icons
      sortableHeaders.forEach(h => {
        if (h !== this) {
          const otherIcon = h.querySelector(".sort-icon");
          otherIcon.textContent = "";
        }
      });

      // Determine sort direction
      if (currentSort.column === column) {
        if (currentSort.direction === "asc") {
          currentSort.direction = "desc";
          icon.textContent = " ↓";
        } else {
          currentSort.direction = "asc";
          icon.textContent = " ↑";
        }
      } else {
        currentSort.column = column;
        currentSort.direction = "asc";
        icon.textContent = " ↑";
      }

      sortTable(column, currentSort.direction);
    });
  });

  function sortTable(column, direction) {
    const rowsArray = Array.from(rows);
    const tbody = table.querySelector("tbody");

    rowsArray.sort((a, b) => {
      let aValue = a.getAttribute(`data-${column}`);
      let bValue = b.getAttribute(`data-${column}`);

      // Handle numeric sorting for rounds
      if (column === "round") {
        const aNum = parseInt(aValue, 10);
        const bNum = parseInt(bValue, 10);
        
        if (!isNaN(aNum) && !isNaN(bNum)) {
          return direction === "asc" ? aNum - bNum : bNum - aNum;
        }
      }

      // Handle timestamp sorting
      if (column === "timestamp") {
        const order = ["Today", "Yesterday"];
        const aIndex = order.findIndex(o => aValue.startsWith(o));
        const bIndex = order.findIndex(o => bValue.startsWith(o));
        
        if (aIndex !== -1 && bIndex !== -1) {
          if (aIndex !== bIndex) {
            return direction === "asc" ? aIndex - bIndex : bIndex - aIndex;
          }
        } else if (aIndex !== -1) {
          return direction === "asc" ? -1 : 1;
        } else if (bIndex !== -1) {
          return direction === "asc" ? 1 : -1;
        }
      }

      // String sorting
      aValue = aValue.toLowerCase();
      bValue = bValue.toLowerCase();

      if (aValue < bValue) return direction === "asc" ? -1 : 1;
      if (aValue > bValue) return direction === "asc" ? 1 : -1;
      return 0;
    });

    // Reorder rows in the DOM
    rowsArray.forEach(row => tbody.appendChild(row));
  }
});
