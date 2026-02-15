const SSGSEA_API = "/api/ssgsea";

const exprEl = document.getElementById("expr-file");
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
const statusText = document.getElementById("status-text");

const MAX_BYTES = 30 * 1024 * 1024;
let downloadUrl = null;
const ALLOWED_EXT = [".tsv", ".txt", ".csv"];
let progressTimer = null;

function hasAllowedExt(filename) {
  const lower = (filename || "").toLowerCase();
  return ALLOWED_EXT.some((ext) => lower.endsWith(ext));
}

function setStatus(message, isError = false) {
  if (statusText) {
    statusText.textContent = message;
  } else {
    statusEl.textContent = message;
  }
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

function resetForm() {
  if (exprEl) exprEl.value = "";
  if (gmtEl) gmtEl.value = "";
  downloadUrl = null;
  if (downloadBtn) downloadBtn.disabled = true;
  if (runBtn) {
    runBtn.disabled = false;
    runBtn.textContent = "Run ssGSEA";
  }
  setStatus("");
  stopProgressPulse(0);
  if (warnIcon) warnIcon.classList.add("hidden");
  if (warnText) warnText.classList.add("hidden");
}

async function runSsgsea() {
  setStatus("");
  downloadUrl = null;
  downloadBtn.disabled = true;
  stopProgressPulse(0);
  if (warnIcon) warnIcon.classList.add("hidden");
  if (warnText) warnText.classList.add("hidden");

  const exprFile = exprEl?.files?.[0];
  const gmtFile = gmtEl?.files?.[0];

  if (!exprFile) {
    setStatus("Upload an expression matrix first.", true);
    setProgress(0, true);
    return;
  }

  if (!hasAllowedExt(exprFile.name)) {
    setStatus("Invalid file type. Use .tsv, .txt, or .csv only.", true);
    setProgress(0, true);
    return;
  }

  if (!gmtFile) {
    setStatus("Upload a GMT file first.", true);
    setProgress(0, true);
    return;
  }

  if (exprFile.size > MAX_BYTES) {
    setStatus("File exceeds the 30 MB capacity.", true);
    setProgress(0, true);
    return;
  }

  runBtn.disabled = true;
  runBtn.textContent = "Running...";
  startProgressPulse();

  try {
    const formData = new FormData();
    formData.append("expression", exprFile);
    formData.append("gmt", gmtFile);

    const res = await fetch(`${SSGSEA_API}/run`, {
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
      setStatus(data.error || "ssGSEA failed.", true);
      stopProgressPulse(0, true);
      return;
    }

    downloadUrl = data.download_url || null;
    if (downloadUrl) {
      downloadBtn.disabled = false;
      setStatus("ssGSEA complete. Click Download results.");
      stopProgressPulse(100);
      if ((data.low_overlap_sets || 0) > 0) {
        if (warnIcon) warnIcon.classList.remove("hidden");
        if (warnText) warnText.classList.remove("hidden");
      }
    } else {
      setStatus("No results returned.", true);
      stopProgressPulse(0, true);
    }
  } catch (err) {
    setStatus(`Error: ${err.message}`, true);
    stopProgressPulse(0, true);
  } finally {
    runBtn.disabled = false;
    runBtn.textContent = "Run ssGSEA";
  }
}

runBtn.addEventListener("click", runSsgsea);
if (resetBtn) resetBtn.addEventListener("click", resetForm);

downloadBtn.addEventListener("click", () => {
  if (!downloadUrl) {
    setStatus("No results ready to download.", true);
    return;
  }

  const link = document.createElement("a");
  link.href = downloadUrl;
  link.download = "ssgsea_scores.csv";
  document.body.appendChild(link);
  link.click();
  link.remove();
});
