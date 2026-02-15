// DEG API base (matches: app.register_blueprint(deg_bp, url_prefix="/api/deg"))
const DEG_API = "/api/deg";

let currentFileId = null;

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

function resetForm() {
  currentFileId = null;
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
  setProgress(0);
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
  currentFileId = null;
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
    // ✅ FIXED: use blueprint endpoint
    const res = await fetch(`${DEG_API}/columns`, {
      method: "POST",
      body: formData,
    });

    // safer parsing (still works when backend returns JSON)
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

    currentFileId = data.file_id;
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

  if (!currentFileId) {
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
  setProgress(25);

  try {
    // ✅ FIXED: use blueprint endpoint
    const res = await fetch(`${DEG_API}/export`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        file_id: currentFileId,
        group_map: groupMap,
        method: methodSelect.value,
        min_count: minCountInput.value,
      }),
    });

    if (!res.ok) {
      // backend returns JSON error
      const text = await res.text();
      let data;
      try {
        data = JSON.parse(text);
      } catch {
        data = { error: text };
      }
      setStatus(data.error || "Export failed.", true);
      setProgress(0, true);
      return;
    }

    setProgress(70);
    const blob = await res.blob();
    downloadUrl = window.URL.createObjectURL(blob);

    if (downloadBtn) downloadBtn.disabled = false;

    setStatus("Comparison complete. Click Download results.");
    setProgress(100);
  } catch (err) {
    setStatus(`Error: ${err.message}`, true);
    setProgress(0, true);
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
