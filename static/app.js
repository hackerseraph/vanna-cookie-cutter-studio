import { MeshViewer } from "./viewer.js";

const $ = (id) => document.getElementById(id);
const form = $("design-form");
let selectedFile = null;
let result = null;
let artworkUrl = null;
let viewer = null;
let generating = false;
let bambuddyBusy = false;
let bambuddyConfigured = false;
let viewRequest = 0;
const meshCache = new Map();

function status(message, error = false) {
  $("status").textContent = message;
  $("status").className = error ? "error" : "success";
}

function invalidate() {
  result = null;
  updateBambuddyControls();
  viewRequest++;
  meshCache.clear();
  $("result-details").hidden = true;
  $("preview-toolbar").hidden = true;
  $("mesh-preview").hidden = true;
  $("view-caption").hidden = true;
  $("art-preview").hidden = true;
  $("empty-state").hidden = false;
  $("preview-badge").textContent = "YOUR WORKBENCH";
  if (artworkUrl) URL.revokeObjectURL(artworkUrl);
  artworkUrl = null;
  status(selectedFile ? "Ready to generate. Your settings will be applied to both tools." : "");
}

function selectFile(file, exampleName = null) {
  if (!file || generating || bambuddyBusy) return;
  if (file.size > 12 * 1024 * 1024) {
    status("Choose an image smaller than 12 MB.", true);
    return;
  }
  selectedFile = file;
  $("upload-title").textContent = file.name;
  $("upload-description").textContent = "Click or drop another image to replace";
  $("generate").disabled = false;
  document.querySelectorAll(".example-button").forEach((button) => {
    button.classList.toggle("selected", button.dataset.name === exampleName);
  });
  invalidate();
}

$("image-input").addEventListener("change", (event) => selectFile(event.target.files[0]));
for (const name of ["dragenter", "dragover"]) {
  $("drop-zone").addEventListener(name, (event) => {
    event.preventDefault();
    if (!generating && !bambuddyBusy) $("drop-zone").classList.add("dragging");
  });
}
for (const name of ["dragleave", "drop"]) {
  $("drop-zone").addEventListener(name, (event) => {
    event.preventDefault();
    $("drop-zone").classList.remove("dragging");
    if (name === "drop") selectFile(event.dataTransfer.files[0]);
  });
}
form.addEventListener("input", (event) => {
  if (event.target.id === "image-input") return;
  const depth = Number($("depth").value);
  $("stamp-depth").value = Number.isFinite(depth) ? String(Number((depth / 4).toFixed(3))) : "—";
  $("threshold-value").value = $("threshold").value;
  $("bolden-value").value = `${Number($("bolden").value).toFixed(2)} mm`;
  invalidate();
});

async function responseJson(response) {
  let payload;
  try {
    payload = await response.json();
  } catch {
    throw new Error(`The server returned an unreadable response (${response.status}). Check the server log.`);
  }
  if (!response.ok) throw new Error(payload.error || `Request failed (${response.status}).`);
  return payload;
}

async function loadExamples() {
  try {
    const names = await responseJson(await fetch("/api/examples"));
    for (const name of names) {
      const button = document.createElement("button");
      button.type = "button";
      button.className = "example-button";
      button.dataset.name = name;
      button.title = `Use ${name.replaceAll("_", " ").replace(".png", "")}`;
      button.setAttribute("aria-label", button.title);
      const image = document.createElement("img");
      image.src = `/api/examples/${encodeURIComponent(name)}`;
      image.alt = "";
      button.append(image);
      button.addEventListener("click", async () => {
        button.disabled = true;
        try {
          const response = await fetch(image.src);
          if (!response.ok) throw new Error("Could not load the example image.");
          selectFile(new File([await response.blob()], name, { type: "image/png" }), name);
        } catch (error) {
          status(error.message, true);
        } finally {
          button.disabled = false;
        }
      });
      $("examples").append(button);
    }
  } catch (error) {
    status(`Examples unavailable: ${error.message} You can still upload an image.`, true);
  }
}

async function showView(kind) {
  if (!result) return;
  const currentResult = result;
  const request = ++viewRequest;
  document.querySelectorAll("[data-view]").forEach((button) => {
    button.classList.toggle("active", button.dataset.view === kind);
    button.setAttribute("aria-pressed", String(button.dataset.view === kind));
  });
  $("art-preview").hidden = kind !== "art";
  $("mesh-preview").hidden = kind === "art";
  $("view-caption").hidden = kind === "art";
  if (kind === "art") return;
  try {
    if (!viewer) viewer = new MeshViewer($("mesh-preview"));
    if (!meshCache.has(kind)) {
      const response = await fetch(currentResult.tools[kind].url);
      if (!response.ok) throw new Error("Could not load the STL preview. Try generating again.");
      const data = await response.arrayBuffer();
      if (request !== viewRequest || result !== currentResult) return;
      meshCache.set(kind, data);
    }
    if (request !== viewRequest) return;
    viewer.load(meshCache.get(kind), kind);
  } catch (error) {
    if (request !== viewRequest) return;
    status(error.message, true);
    showView("art");
  }
}

document.querySelectorAll("[data-view]").forEach((button) => {
  button.addEventListener("click", () => showView(button.dataset.view));
});

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  if (!selectedFile || generating || bambuddyBusy) return;
  invalidate();
  generating = true;
  $("controls").disabled = true;
  $("loading-overlay").hidden = false;
  $("preview-stage").setAttribute("aria-busy", "true");
  status("Generating your matched tools. Detailed images may take a moment.");
  const data = new FormData();
  data.set("image", selectedFile);
  data.set("size_mm", String(Number($("size").value) * 25.4));
  data.set("cutter_depth_mm", $("depth").value);
  data.set("threshold", $("threshold").value);
  data.set("invert", String($("invert").checked));
  data.set("bolden_mm", $("bolden").value);
  try {
    result = await responseJson(await fetch("/api/generate", { method: "POST", body: data }));
    artworkUrl = URL.createObjectURL(new Blob([result.preview_svg], { type: "image/svg+xml" }));
    $("art-preview").src = artworkUrl;
    $("empty-state").hidden = true;
    $("preview-toolbar").hidden = false;
    $("result-details").hidden = false;
    updateBambuddyControls();
    $("preview-badge").textContent = "YOUR DESIGN, READY";
    $("scale-note").textContent = `${result.cookie_mm.map((n) => n.toFixed(1)).join(" × ")} mm`;
    $("cutter-meta").textContent = `STL · ${result.cutter_depth_mm} mm deep`;
    $("stamp-meta").textContent = `STL · ${result.stamp_impression_mm} mm impression`;
    for (const kind of ["cutter", "stamp"]) {
      $(`download-${kind}`).href = result.tools[kind].url;
      $(`download-${kind}`).setAttribute("download", result.tools[kind].filename);
      $(`download-${kind}`).title = `Overall tool dimensions: ${result.tools[kind].dimensions_mm.map((n) => n.toFixed(1)).join(" × ")} mm`;
    }
    $("download-bundle").href = `/api/designs/${result.id}/bundle`;
    $("download-bundle").setAttribute("download", `${result.name}-tools.zip`);
    $("warnings").replaceChildren(...result.warnings.map((warning) => {
      const item = document.createElement("li");
      item.textContent = warning;
      return item;
    }));
    status("Your cutter and stamp are ready. Download, slice in millimeters, and print backing-side down.");
    await showView("art");
  } catch (error) {
    status(error.message, true);
  } finally {
    generating = false;
    $("controls").disabled = false;
    $("loading-overlay").hidden = true;
    $("preview-stage").setAttribute("aria-busy", "false");
  }
});

function bambuddyStatus(message, error = false) {
  $("bambuddy-status").textContent = message;
  $("bambuddy-status").className = error ? "error" : "";
}

function updateBambuddyControls() {
  $("bambuddy-controls").hidden = !bambuddyConfigured;
  for (const id of ["bambuddy-kind", "refresh-bambuddy", "test-bambuddy"]) {
    $(id).disabled = bambuddyBusy;
  }
  $("send-bambuddy").disabled = bambuddyBusy || !bambuddyConfigured || !result;
}

async function loadBambuddy() {
  setBambuddyBusy(true);
  try {
    const config = await responseJson(await fetch("/api/bambuddy"));
    bambuddyConfigured = config.configured;
    if (config.library_url) $("open-bambuddy").href = config.library_url;
    bambuddyStatus(config.message);
  } catch (error) {
    bambuddyConfigured = false;
    $("open-bambuddy").removeAttribute("href");
    bambuddyStatus(error.message, true);
  } finally {
    setBambuddyBusy(false);
  }
}

function setBambuddyBusy(busy) {
  bambuddyBusy = busy;
  $("controls").disabled = busy || generating;
  document.querySelectorAll(".example-button").forEach((button) => { button.disabled = busy; });
  updateBambuddyControls();
}

$("refresh-bambuddy").addEventListener("click", loadBambuddy);
$("test-bambuddy").addEventListener("click", async () => {
  if (bambuddyBusy) return;
  setBambuddyBusy(true);
  bambuddyStatus("Checking Bambuddy on your local network...");
  try {
    const connection = await responseJson(await fetch("/api/bambuddy/test", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: "{}",
    }));
    bambuddyStatus(connection.message);
  } catch (error) {
    bambuddyStatus(error.message, true);
  } finally {
    setBambuddyBusy(false);
  }
});
$("send-bambuddy").addEventListener("click", async () => {
  if (!result || bambuddyBusy) return;
  setBambuddyBusy(true);
  bambuddyStatus("Sending models to Bambuddy. No print will be started.");
  try {
    const upload = await responseJson(await fetch("/api/bambuddy/send", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ design_id: result.id, kind: $("bambuddy-kind").value }),
    }));
    $("open-bambuddy").href = upload.library_url;
    bambuddyStatus(`${upload.message} Files: ${upload.files.map((file) =>
      `${file.filename}${file.duplicate_of ? " (duplicate already in library)" : ""}`).join(", ")}`);
  } catch (error) {
    bambuddyStatus(error.message, true);
  } finally {
    setBambuddyBusy(false);
  }
});

loadExamples();
loadBambuddy();
