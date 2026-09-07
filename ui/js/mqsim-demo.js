/**
 * Demo bootstrap for Merge Queue Sim.
 *
 * ui/demo/demo-runs.js assigns window.MQSIM_DEMO_RUNS, a three-policy
 * comparison. This feeds it through the same loadFiles() path a dropped file
 * takes, so the page opens on a real run instead of an empty drop zone.
 * The page cannot fetch the data instead: its CSP sets connect-src 'none'.
 */
(function () {
  function demoFiles() {
    const runs = window.MQSIM_DEMO_RUNS;
    if (!Array.isArray(runs) || !runs.length) return null;
    if (typeof File !== "function") return null;
    return runs.map(r => new File([r.text], r.name, { type: "application/x-ndjson" }));
  }

  function loadDemo() {
    const files = demoFiles();
    if (!files || typeof loadFiles !== "function") return false;
    loadFiles(files);
    return true;
  }

  window.mqsimLoadDemo = loadDemo;

  const btn = document.getElementById("btnLoadDemo");
  if (btn) {
    if (!demoFiles()) btn.disabled = true;
    else btn.addEventListener("click", loadDemo);
  }

  // Auto-load on open, but never over data the viewer already loaded.
  if (typeof filesData !== "undefined" && Array.isArray(filesData) && filesData.length) return;
  loadDemo();
})();
