// DEG API base (matches: app.register_blueprint(deg_bp, url_prefix="/api/deg"))
const DEG_API = "/api/deg";

// ✅ store upload job_id (not file_id)
let currentUploadJobId = null;

const fileInput = document.getElementById("file");
const loadBtn = document.getElementById("load-btn");
const resetBtn = document.getElementById("reset-btn");
const statusEl = document.getElementById("status");
const samplesCard = document.getElementById("samples-card");
const samplesList = document.getElementById("samples-list");
const exportBtn = document.getElementById("export-btn");
const downloadBtn = document.getElementById("download-btn");
const methodSelect = document.getElementById("method");
const minCountInput = document.getElementById("min-count");
const statusBar = document.getElementById("status-bar");
const statusBarFill = document.getElementById("status-bar-fill");
const statusPercent = document.getElementById("status-percent");

let downloadUrl = null;
let progressTimer = null;

function setStatus(message, isError = false) {
  statusEl.textContent = message;
  statusEl.classList.toggle("error", isError);
}

function setProgress(value, isError = false) {
  if (!statusBarFill || !statusBar) return;

  const safeValue = Math.max(0, Math.min(100, Number(value) || 0));
  statusBarFill.style.width = `${safeValue}%`;
  statusBar.classList.toggle("error", isError);

  if (statusPercent) {
    statusPercent.textContent = `${Math.round(safeValue)}%`;
  }
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
  currentUploadJobId = null; // ✅
  fileInput.value = "";
  samplesList.innerHTML = "";
  samplesCard.classList.add("hidden");
  methodSelect.value = "edger";
  minCountInput.value = "2";
  loadBtn.disabled = false;
  loadBtn.textContent = "Load samples";
  exportBtn.disabled = false;
  exportBtn.textContent = "Run";

  if (downloadUrl) {
    window.URL.revokeObjectURL(downloadUrl);
    downloadUrl = null;
  }
  if (downloadBtn) downloadBtn.disabled = true;

  setStatus("");
  stopProgressPulse(0);
}

function renderSamples(samples) {
  samplesList.innerHTML = "";

  samples.forEach((name) => {
    const row = document.createElement("div");
    row.className = "sample-row";

    const label = document.createElement("div");
    label.className = "sample-name";
    label.textContent = name;

    const select = document.createElement("select");
    select.className = "sample-select";

    [
      { value: "ignore", label: "Ignore" },
      { value: "A", label: "Group A" },
      { value: "B", label: "Group B" },
    ].forEach((opt) => {
      const option = document.createElement("option");
      option.value = opt.value;
      option.textContent = opt.label;
      select.appendChild(option);
    });

    row.appendChild(label);
    row.appendChild(select);
    samplesList.appendChild(row);
  });
}

function getGroupMap() {
  const map = {};
  Array.from(samplesList.querySelectorAll(".sample-row")).forEach((row) => {
    const name = row.querySelector(".sample-name").textContent;
    const val = row.querySelector("select").value;
    if (val === "A" || val === "B") map[name] = val;
  });
  return map;
}

if (resetBtn) {
  resetBtn.addEventListener("click", resetForm);
}

loadBtn.addEventListener("click", async () => {
  setStatus("");
  samplesCard.classList.add("hidden");
  currentUploadJobId = null; // ✅
  setProgress(0);

  const file = fileInput.files[0];
  if (!file) {
    setStatus("Please select a CSV file first.", true);
    setProgress(0, true);
    return;
  }

  const formData = new FormData();
  formData.append("file", file);

  loadBtn.disabled = true;
  loadBtn.textContent = "Loading...";
  setProgress(15);

  try {
    const res = await fetch(`${DEG_API}/columns`, {
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
      setStatus(data.error || "Failed to read file.", true);
      setProgress(0, true);
      return;
    }

    // ✅ backend now returns job_id (staged upload job)
    currentUploadJobId = data.job_id;

    if (!currentUploadJobId) {
      setStatus(data.error || "Upload staging failed.", true);
      setProgress(0, true);
      return;
    }

    renderSamples(data.sample_cols || []);
    samplesCard.classList.remove("hidden");
    setStatus(`Loaded ${(data.sample_cols || []).length} samples.`);
    setProgress(100);
  } catch (err) {
    setStatus(`Error: ${err.message}`, true);
    setProgress(0, true);
  } finally {
    loadBtn.disabled = false;
    loadBtn.textContent = "Load samples";
  }
});

exportBtn.addEventListener("click", async () => {
  setStatus("");

  if (downloadUrl) {
    window.URL.revokeObjectURL(downloadUrl);
    downloadUrl = null;
  }
  if (downloadBtn) downloadBtn.disabled = true;

  if (!currentUploadJobId) {
    setStatus("Upload a file first.", true);
    setProgress(0, true);
    return;
  }

  const groupMap = getGroupMap();
  if (!Object.values(groupMap).includes("A") || !Object.values(groupMap).includes("B")) {
    setStatus("Assign at least one sample to Group A and Group B.", true);
    setProgress(0, true);
    return;
  }

  exportBtn.disabled = true;
  exportBtn.textContent = "Running...";
  startProgressPulse();

  try {
    const res = await fetch(`${DEG_API}/export`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        // ✅ send job_id (not file_id)
        job_id: currentUploadJobId,
        group_map: groupMap,
        method: methodSelect.value,
        min_count: minCountInput.value,
      }),
    });

    const text = await res.text();
    let data;
    try {
      data = JSON.parse(text);
    } catch {
      data = { error: text };
    }

    if (!res.ok) {
      setStatus(data.error || "Export failed.", true);
      stopProgressPulse(0, true);
      return;
    }

    const jobId = data.job_id;
    if (!jobId) {
      setStatus(data.error || "Job submission failed.", true);
      stopProgressPulse(0, true);
      return;
    }

    setStatus("Job queued. Running analysis...");
    const result = await waitForJob(jobId);

    // In your job result, deg.py returns {"download_url": "..."} for export job
    downloadUrl = result.download_url || null;

    if (downloadUrl) {
      if (downloadBtn) downloadBtn.disabled = false;
      setStatus("Comparison complete. Click Download results.");
      stopProgressPulse(100);
    } else {
      setStatus("No results returned.", true);
      stopProgressPulse(0, true);
    }
  } catch (err) {
    setStatus(`Error: ${err.message}`, true);
    stopProgressPulse(0, true);
  } finally {
    exportBtn.disabled = false;
    exportBtn.textContent = "Run";
  }
});

if (downloadBtn) {
  downloadBtn.addEventListener("click", () => {
    if (!downloadUrl) {
      setStatus("No results ready to download.", true);
      setProgress(0, true);
      return;
    }

    const link = document.createElement("a");
    link.href = downloadUrl;
    link.download = "de_results.csv";
    document.body.appendChild(link);
    link.click();
    link.remove();
  });
}
