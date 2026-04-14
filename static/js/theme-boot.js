(function () {
  try {
    var pref = localStorage.getItem("nr-theme");
    if (pref === "light" || pref === "dark") {
      document.documentElement.setAttribute("data-theme", pref);
      return;
    }
    var server = document.documentElement.getAttribute("data-theme-server");
    if (server === "light" || server === "dark") {
      document.documentElement.setAttribute("data-theme", server);
    }
  } catch (error) {
    // Theme selection is best-effort; private browsing can block storage.
  }
})();
