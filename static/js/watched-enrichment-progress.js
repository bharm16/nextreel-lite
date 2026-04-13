(function () {
  var grid = document.getElementById("watched-grid");
  if (!grid) return;

  var pollInterval = 10000;
  var maxPollDuration = 600000;
  var startTime = Date.now();

  function appendCards(html) {
    if (!html) return;

    var temp = document.createElement("div");
    temp.innerHTML = html;
    var cards = temp.querySelectorAll(".watched-card");
    cards.forEach(function (card) {
      grid.appendChild(card);
    });

    window.dispatchEvent(
      new CustomEvent("nextreel:watched-cards-added", {
        detail: { added: cards.length },
      })
    );
  }

  function pollProgress() {
    if (Date.now() - startTime > maxPollDuration) {
      return;
    }

    fetch("/watched/enrichment-progress", {
      credentials: "same-origin",
      headers: { Accept: "application/json" },
    }).then(function (response) {
      if (!response.ok) return null;
      return response.json();
    }).then(function (data) {
      if (!data) return;

      appendCards(data.html);

      if (!data.done) {
        setTimeout(pollProgress, pollInterval);
      }
    }).catch(function () {
      setTimeout(pollProgress, pollInterval);
    });
  }

  setTimeout(pollProgress, pollInterval);
})();
