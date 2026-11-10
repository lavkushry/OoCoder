/* Minimal Chart stand-in so stats.html stays offline and self-contained.
   Tables in the page already carry the same numbers. */
function Chart(canvas, config) {
  this.canvas = canvas;
  this.config = config;
}
Chart.defaults = {};
globalThis.Chart = Chart;
