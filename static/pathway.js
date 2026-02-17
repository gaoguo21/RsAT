const PATHWAY_API = "/api/pathway";

const fileEl = document.getElementById("gene-file");
const organismEl = document.getElementById("organism");
const libraryEl = document.getElementById("library");
const gmtEl = document.getElementById("gmt-file");
const runBtn = document.getElementById("run-btn");
const resetBtn = document.getElementById("reset-btn");
const statusEl = document.getElementById("status");
const downloadBtn = document.getElementById("download-btn");
const statusBar = document.getElementById("status-bar");
const statusBarFill = document.getElementById("status-bar-fill");
const statusPercent = document.getElementById("status-percent");
const warnIcon = document.getElementById("warn-icon");
const warnText = document.getElementById("warn-text");

let downloadUrl = null;
let progressTimer = null;
let currentJobId = null;

function setStatus(message, isError = false) {
  statusEl.textContent = message;
  statusEl.classList.toggle("error", isError);
}

function setProgress(value, isError = false) {
  if (!statusBarFill || !statusBar) return;
  const safeValue = Math.max(0, Math.min(100, Number(value) || 0));
  statusBarFill.style.width = `${safeValue}%`;
  statusBar.classList.toggle("error", isError);
  if (statusPercent) statusPercent.textContent = `${Math.round(safeValue)}%`;
}

function startProgressPulse(start = 20, max = 85) {
  clearInterval(progressTimer);
  let current = start;
  setProgress(current);
  progressTimer = setInterval(() => {
    current += Math.random() * 4;
    if (current > max) current = max;
    setProgress(current);
  }, 350);
}

function stopProgressPulse(finalValue, isError = false) {
  clearInterval(progressTimer);
  progressTimer = null;
  setProgress(finalValue, isError);
}

async function waitForJob(jobId) {
  const statusUrl = `/job/${jobId}/status`;
  while (true) {
    const res = await fetch(statusUrl);
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
      throw new Error(data.error || "Failed to fetch job status.");
    }
    if (data.status === "finished") return data.result || {};
    if (data.status === "failed") {
      throw new Error(data.error || "Job failed.");
    }
    await new Promise((resolve) => setTimeout(resolve, 1000));
  }
}

function resetForm() {
  if (fileEl) fileEl.value = "";
  if (gmtEl) gmtEl.value = "";
  if (organismEl) organismEl.value = "human";
  if (libraryEl) libraryEl.value = "kegg";
  downloadUrl = null;
  currentJobId = null;
  if (downloadBtn) downloadBtn.disabled = true;
  if (runBtn) {
    runBtn.disabled = false;
    runBtn.textContent = "Run enrichment";
  }
  setStatus("");
  stopProgressPulse(0);
  if (warnIcon) warnIcon.classList.add("hidden");
  if (warnText) warnText.classList.add("hidden");
}

async function runEnrichment() {
  setStatus("");
  downloadUrl = null;
  downloadBtn.disabled = true;
  stopProgressPulse(0);
  if (warnIcon) warnIcon.classList.add("hidden");
  if (warnText) warnText.classList.add("hidden");

  const file = fileEl?.files?.[0];
  if (!file) {
    setStatus("Upload a preranked gene file first.", true);
    setProgress(0, true);
    return;
  }

  if (libraryEl.value === "custom" && !gmtEl?.files?.[0]) {
    setStatus("Upload a GMT file for the Custom dataset.", true);
    setProgress(0, true);
    return;
  }

  runBtn.disabled = true;
  runBtn.textContent = "Running...";
  startProgressPulse();

  try {
    const formData = new FormData();
    formData.append("file", file);
    formData.append("organism", organismEl.value);
    formData.append("library", libraryEl.value);
    if (gmtEl?.files?.[0]) formData.append("gmt", gmtEl.files[0]);

    const res = await fetch(`${PATHWAY_API}/run`, {
      method: "POST",
      body: formData,
    });

    const text = await res.text();
    let data;
    try {
      data = JSON.parse(text);
    } catch {
      data = { error: text };
    }

    if (!res.ok) {
      const rawError = data.error || "Enrichment failed.";
      const genericNoPathways =
        "No pathways detected. The gene list may be too short or lacks sufficient pathway overlap.";
      const errorText =
        /no pathways|filtered out|min_report_size/i.test(rawError) ? genericNoPathways : rawError;
      setStatus(errorText, true);
      stopProgressPulse(0, true);
      if (warnIcon) warnIcon.classList.add("hidden");
      if (warnText) warnText.classList.add("hidden");
      return;
    }

    currentJobId = data.job_id;
    if (!currentJobId) {
      setStatus(data.error || "Job submission failed.", true);
      stopProgressPulse(0, true);
      return;
    }

    setStatus("Job queued. Running enrichment...");
    const result = await waitForJob(currentJobId);
    downloadUrl = result.download_url || null;

    if (downloadUrl) {
      downloadBtn.disabled = false;
      setStatus("Enrichment complete. Click Download results.");
      stopProgressPulse(100);
      if (warnIcon) warnIcon.classList.add("hidden");
      if (warnText) warnText.classList.add("hidden");
    } else {
      setStatus("No results returned.", true);
      stopProgressPulse(0, true);
      if (warnIcon) warnIcon.classList.remove("hidden");
      if (warnText) warnText.classList.remove("hidden");
    }
  } catch (err) {
    setStatus(`Error: ${err.message}`, true);
    stopProgressPulse(0, true);
  } finally {
    runBtn.disabled = false;
    runBtn.textContent = "Run enrichment";
  }
}

runBtn.addEventListener("click", runEnrichment);
if (resetBtn) resetBtn.addEventListener("click", resetForm);

downloadBtn.addEventListener("click", () => {
  if (!downloadUrl) {
    setStatus("No results ready to download.", true);
    return;
  }

  const link = document.createElement("a");
  link.href = downloadUrl;
  link.download = "pathway_results.csv";
  document.body.appendChild(link);
  link.click();
  link.remove();
});
