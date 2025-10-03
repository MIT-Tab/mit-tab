import List from "list.js";

document.addEventListener("DOMContentLoaded", () => {
  if (!document.getElementById("backup-table")) return;

  const list = new List("backup-table", {
    valueNames: ["name", "type", "round", "timestamp", "scratches"]
  });

  const filter = () => {
    const get = id => document.getElementById(`${id}-filter`).value;
    const [t, r, s] = ["type", "round", "scratches"].map(get);
    list.filter(item => {
      const { type, round, scratches } = item.elm.dataset;
      return (
        (!t || type === t) && (!r || round === r) && (!s || scratches === s)
      );
    });
  };

  ["type", "round", "scratches"].forEach(id =>
    document.getElementById(`${id}-filter`).addEventListener("change", filter)
  );
});
