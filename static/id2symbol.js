const ID2SYMBOL_API = "/api/id2symbol";

const fileEl = document.getElementById("id-file");
const organismEl = document.getElementById("organism");
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

let downloadUrl = null;
let progressTimer = null;

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
  if (fileEl) fileEl.value = "";
  if (organismEl) organismEl.value = "human";
  downloadUrl = null;
  if (downloadBtn) downloadBtn.disabled = true;
  if (runBtn) {
    runBtn.disabled = false;
    runBtn.textContent = "Convert";
  }
  setStatus("");
  stopProgressPulse(0);
  if (warnIcon) warnIcon.classList.add("hidden");
  if (warnText) warnText.classList.add("hidden");
}

async function runConversion() {
  setStatus("");
  downloadUrl = null;
  downloadBtn.disabled = true;
  stopProgressPulse(0);
  if (warnIcon) warnIcon.classList.add("hidden");
  if (warnText) warnText.classList.add("hidden");

  const file = fileEl?.files?.[0];
  if (!file) {
    setStatus("Upload a gene ID file first.", true);
    setProgress(0, true);
    return;
  }

  runBtn.disabled = true;
  runBtn.textContent = "Converting...";
  startProgressPulse();

  try {
    const formData = new FormData();
    formData.append("file", file);
    formData.append("organism", organismEl.value);

    const res = await fetch(`${ID2SYMBOL_API}/run`, {
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
      setStatus(data.error || "Conversion failed.", true);
      stopProgressPulse(0, true);
      return;
    }

    downloadUrl = data.download_url || null;
    if (downloadUrl) {
      downloadBtn.disabled = false;
      setStatus(`Mapped ${data.mapped} / ${data.total} IDs. Click Download results.`);
      stopProgressPulse(100);
      if ((data.unmapped || 0) > 0) {
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
    runBtn.textContent = "Convert";
  }
}

runBtn.addEventListener("click", runConversion);
if (resetBtn) resetBtn.addEventListener("click", resetForm);

downloadBtn.addEventListener("click", () => {
  if (!downloadUrl) {
    setStatus("No results ready to download.", true);
    return;
  }

  const link = document.createElement("a");
  link.href = downloadUrl;
  link.download = "id2symbol_results.csv";
  document.body.appendChild(link);
  link.click();
  link.remove();
});
