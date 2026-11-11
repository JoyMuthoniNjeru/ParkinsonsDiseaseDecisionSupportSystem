// Text size toggle (partials/text_size.html). The size itself is applied in base.html's <head>
// before the page draws (no flash of small text); this file wires up the A / A+ / A++ buttons.
(function () {
  var KEY = "pd-text-size";
  var root = document.documentElement;

  function current() {
    return root.getAttribute("data-text-size") || "normal";
  }

  function show() {
    document.querySelectorAll(".ts-btn").forEach(function (btn) {
      btn.setAttribute("aria-pressed", btn.dataset.textSize === current() ? "true" : "false");
    });
    // Bigger text doesn't fit across the top bar, so the menu folds into the ☰ button instead
    var nav = document.getElementById("pd-navbar");
    if (nav) nav.classList.toggle("navbar-expand-xxl", current() === "normal");
  }

  document.querySelectorAll(".ts-btn").forEach(function (btn) {
    btn.addEventListener("click", function () {
      var size = btn.dataset.textSize;
      if (size === "normal") {
        root.removeAttribute("data-text-size");
      } else {
        root.setAttribute("data-text-size", size);
      }
      try { localStorage.setItem(KEY, size); } catch (e) { /* private window: just this page */ }
      show();
    });
  });

  show();
})();
