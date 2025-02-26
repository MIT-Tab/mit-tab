import quickSearchInit from "./quickSearch";

document.addEventListener("DOMContentLoaded", function () {
  const categorySelect = document.getElementById("categorySelect");

  const checkinContainers = {
    team: document.getElementById("teamCheckin"),
    judge: document.getElementById("judgeCheckin"),
    room: document.getElementById("roomCheckin"),
  };

  const searchInputs = {
    team: document.getElementById("team-search"),
    judge: document.getElementById("judge-search"),
    room: document.getElementById("room-search"),
  };

  const savedSelection = localStorage.getItem("selectedCategory") || "team";
  categorySelect.value = savedSelection;
  updateCheckinVisibility(savedSelection);

  categorySelect.addEventListener("change", function () {
    localStorage.setItem("selectedCategory", this.value);
    updateCheckinVisibility(this.value);
  });

  function updateCheckinVisibility(selected) {
    Object.values(checkinContainers).forEach(el => el.classList.add("hidden"));
    checkinContainers[selected].classList.remove("hidden");

    quickSearchInit(searchInputs[selected]);
  }

  quickSearchInit(searchInputs[savedSelection]);
});
