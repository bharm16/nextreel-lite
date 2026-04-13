(function () {
  try {
    var pref = localStorage.getItem("nr-theme");
    if (pref === "light" || pref === "dark") {
      document.documentElement.setAttribute("data-theme", pref);
    }
  } catch (error) {
    // Theme selection is best-effort; private browsing can block storage.
  }
})();
