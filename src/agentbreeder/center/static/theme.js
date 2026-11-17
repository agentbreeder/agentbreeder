// The console's theme before first paint (design v2 §10.3, #1116). A classic script listed in
// <head> before the stylesheets, so the page never flashes the wrong palette. It only compares the
// stored preference with three literals: the value is never used as a class name or markup.
// app.js keeps applyTheme and the system listener; tests/test_center_static.py checks that both
// accept exactly the same values ("system", "light", "dark").
(function () {
  var theme = "system";
  try {
    var stored = window.localStorage.getItem("console-theme");
    if (stored === "light" || stored === "dark" || stored === "system") theme = stored;
  } catch (error) {
    theme = "system"; // storage blocked: follow the system
  }
  var dark = theme === "dark";
  if (theme === "system") {
    try {
      dark = window.matchMedia("(prefers-color-scheme: dark)").matches;
    } catch (error) {
      dark = false;
    }
  }
  var root = document.documentElement;
  root.classList.toggle("dark", dark);
  root.style.colorScheme = dark ? "dark" : "light";
  // The browser chrome follows the stored choice too, not only the OS (design v2 §7).
  var metas = document.querySelectorAll('meta[name="theme-color"]');
  for (var i = 0; i < metas.length; i += 1) metas[i].setAttribute("content", dark ? "#0a0a0a" : "#ffffff");
})();
