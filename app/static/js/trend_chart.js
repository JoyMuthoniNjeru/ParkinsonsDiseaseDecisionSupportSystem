// Symptom trend chart (Chart.js 4), used by partials/trend_chart.html.
// Shows one symptom at a time: its daily score on the 0-4 scale over the last 14 days,
// with the Rule Engine's alert threshold (3 = Moderate) drawn as a red reference line.
// The data comes from dashboard_data.symptom_trends(), embedded in #trend-data as JSON.
(function () {
  const dataEl = document.getElementById("trend-data");
  if (!dataEl || !window.Chart) return;  // no data, or Chart.js didn't load (offline)

  const data = JSON.parse(dataEl.textContent);
  const select = document.getElementById("trend-symptom");
  const tableBody = document.querySelector("#trend-table tbody");

  // Colours come from the CSS palette in style.css, so the chart matches the rest of the app
  const css = getComputedStyle(document.documentElement);
  const token = (name) => css.getPropertyValue(name).trim();
  const PLUM = token("--pd-plum");
  const PLUM_DARK = token("--pd-plum-dark");
  const RED = token("--pd-red");
  const MUTED = token("--pd-muted");
  const GRID = "#ECE6ED";      // hairline gridlines, one step off the white card
  const BASELINE = "#C9BCCB";

  Chart.defaults.font.family = "'Atkinson Hyperlegible', system-ui, sans-serif";
  Chart.defaults.font.size = 13;
  Chart.defaults.color = MUTED;

  // e.g. "3 / 4 · Moderate", or "2.5 / 4 · Moderate (average of 2 entries)"
  function describe(value, count) {
    const shown = Number.isInteger(value) ? value : value.toFixed(1);
    const text = `${shown} / ${data.max} · ${data.severity_labels[Math.round(value)]}`;
    return count > 1 ? `${text} (average of ${count} entries)` : text;
  }

  // Draws the alert threshold line and its label underneath the data line
  const thresholdLine = {
    id: "thresholdLine",
    beforeDatasetsDraw(chart) {
      const { ctx, chartArea: area, scales: { y } } = chart;
      const yPos = y.getPixelForValue(data.threshold);
      ctx.save();
      ctx.strokeStyle = RED;
      ctx.lineWidth = 1.5;
      ctx.beginPath();
      ctx.moveTo(area.left, yPos);
      ctx.lineTo(area.right, yPos);
      ctx.stroke();
      ctx.fillStyle = RED;
      ctx.font = "700 12px 'Atkinson Hyperlegible', system-ui, sans-serif";
      // Label at the LEFT end: recent days (right side) are where scores are most likely
      // to sit on the line, and a label there would cover the newest dot
      ctx.textAlign = "left";
      ctx.textBaseline = "bottom";
      ctx.fillText(`Alert at ${data.threshold}`, area.left + 4, yPos - 3);
      ctx.restore();
    },
  };

  let current = data.series[0];

  const chart = new Chart(document.getElementById("trend-canvas"), {
    type: "line",
    data: {
      labels: data.labels,
      datasets: [{
        label: current.name,
        data: current.values,
        borderColor: PLUM,
        borderWidth: 2,
        borderCapStyle: "round",
        borderJoinStyle: "round",
        pointBackgroundColor: PLUM,
        pointBorderColor: "#fff",    // white ring keeps dots readable where they sit on the line
        pointBorderWidth: 2,
        pointRadius: 5,
        pointHoverRadius: 7,
        pointHitRadius: 14,          // bigger than the dot, easier to hover with a tremor
        spanGaps: true,              // join the line across days with no entry (no dot there)
        tension: 0,
        clip: 8,                     // don't cut dots in half at 0 and 4
      }],
    },
    options: {
      maintainAspectRatio: false,
      animation: false,
      interaction: { mode: "nearest", axis: "x", intersect: false },
      scales: {
        y: {
          min: 0,
          max: data.max,
          ticks: { stepSize: 1, callback: (v) => `${v} ${data.severity_labels[v]}` },
          grid: { color: GRID },
          border: { display: false },
        },
        x: {
          ticks: { maxRotation: 0, autoSkip: true, maxTicksLimit: 5 },
          grid: { display: false },
          border: { color: BASELINE },
        },
      },
      plugins: {
        legend: { display: false },  // one series: the dropdown above already names it
        tooltip: {
          backgroundColor: PLUM_DARK,
          padding: 10,
          displayColors: false,
          titleFont: { weight: "700" },
          callbacks: {
            title: (items) => `${current.name}, ${items[0].label}`,
            label: (item) => describe(item.raw, current.counts[item.dataIndex]),
          },
        },
      },
    },
    plugins: [thresholdLine],
  });

  function show(index) {
    current = data.series[index];
    chart.data.datasets[0].label = current.name;
    chart.data.datasets[0].data = current.values;
    chart.update();

    // Table view: only the days with an entry
    tableBody.innerHTML = "";
    current.values.forEach((value, i) => {
      if (value === null) return;
      const row = tableBody.insertRow();
      row.insertCell().textContent = data.labels[i];
      row.insertCell().textContent = describe(value, current.counts[i]);
    });
  }

  select.addEventListener("change", () => show(Number(select.value)));
  show(0);
})();
