export class SheetEditor {
  constructor({ json, settings, showResult, busy }) {
    this.json = json;
    this.settings = settings;
    this.showResult = showResult;
    this.busy = busy;
    this.sheet = null;
    this.crops = [];
    this.active = -1;
    this.results = [];
    this.locked = false;
    this.$ = (id) => document.getElementById(id);
    this.$("add-crop").addEventListener("click", () => this.add());
    this.$("delete-crop").addEventListener("click", () => {
      this.crops.splice(this.active, 1);
      this.active = Math.min(this.active, this.crops.length - 1);
      this.render();
    });
    this.$("crop-list").addEventListener("change", (event) => {
      this.active = Number(event.target.value);
      this.render();
    });
    for (const field of ["name", "size", "selected", "x", "y", "width", "height"]) {
      this.$(`crop-${field}`).addEventListener("change", () => this.edit(field));
    }
    this.$("generate-crop").addEventListener("click", () => this.generate(false));
    this.$("generate-sheet").addEventListener("click", () => this.generate(true));
    this.$("bundle-sheet").addEventListener("click", () => this.bundle());
    this.$("sheet-image").addEventListener("load", () => this.drawPreview());
    this.$("sheet-image").addEventListener("error", () => this.message("Could not load the sheet preview. Upload and detect the sheet again.", true));
    const stage = this.$("sheet-stage");
    stage.addEventListener("pointerdown", (event) => this.pointerDown(event));
    stage.addEventListener("pointermove", (event) => this.pointerMove(event));
    stage.addEventListener("pointerup", () => { this.drag = null; });
    stage.addEventListener("pointercancel", () => { this.drag = null; });
  }

  message(text, error = false) {
    this.$("sheet-status").textContent = text;
    this.$("sheet-status").className = error ? "error" : "";
  }

  reset() {
    this.sheet = null;
    this.crops = [];
    this.results = [];
    this.$("sheet-editor").hidden = true;
    this.$("sheet-results").replaceChildren();
    this.$("download-sheet").hidden = true;
    this.$("bundle-sheet").hidden = true;
  }

  setBusy(value) {
    this.locked = value;
    this.$("sheet-controls").disabled = value;
    this.$("bundle-sheet").disabled = value;
    this.$("sheet-stage").classList.toggle("locked", value);
  }

  async detect(file) {
    if (!file || this.locked) return;
    this.busy(true);
    this.$("sheet-editor").hidden = false;
    this.$("sheet-editor").scrollIntoView({ behavior: "smooth", block: "start" });
    this.message("Detecting outer outlines on this computer...");
    try {
      const data = this.settings();
      data.set("image", file);
      const sheet = await this.json(await fetch("/api/sheets/detect", { method: "POST", body: data }));
      this.reset();
      this.sheet = sheet;
      this.crops = sheet.regions.map((crop) => ({ ...crop, selected: true, inches: crop.size_mm / 25.4 }));
      this.active = this.crops.length ? 0 : -1;
      this.$("sheet-image").src = sheet.image_url;
      this.$("sheet-editor").hidden = false;
      this.render();
      this.message(this.crops.length
        ? `Found ${this.crops.length} designs. Review the crops, names and sizes before generating.`
        : "No closed outlines detected. Add a crop manually, or adjust the image threshold and re-detect.");
    } catch (error) {
      this.message(error.message, true);
    } finally {
      this.busy(false);
    }
  }

  add() {
    if (!this.sheet || this.crops.length >= this.sheet.max_crops) return;
    this.crops.push({
      x: 0, y: 0, width: Math.max(3, Math.round(this.sheet.width / 4)),
      height: Math.max(3, Math.round(this.sheet.height / 4)),
      name: `Design ${this.crops.length + 1}`,
      inches: Number(this.settings().get("size_mm")) / 25.4, selected: true,
    });
    this.active = this.crops.length - 1;
    this.render();
  }

  edit(field) {
    const crop = this.crops[this.active];
    if (!crop) return;
    const input = this.$(`crop-${field}`);
    if (field === "selected") crop.selected = input.checked;
    else if (field === "name") crop.name = input.value;
    else if (field === "size") {
      const size = Number(input.value);
      if (!Number.isFinite(size) || size < 1 || size > 6) {
        this.message("Cookie size must be between 1 and 6 inches.", true);
        this.render();
        return;
      }
      crop.inches = size;
    } else {
      const value = Number(input.value);
      const next = { ...crop, [field]: value };
      if (!Number.isInteger(value) || next.x < 0 || next.y < 0 || next.width < 3 || next.height < 3
          || next.x + next.width > this.sheet.width || next.y + next.height > this.sheet.height) {
        this.message("Crop must stay inside the sheet and be at least 3 pixels wide and high.", true);
        this.render();
        return;
      }
      crop[field] = value;
    }
    this.render();
  }

  render() {
    const crop = this.crops[this.active];
    this.$("crop-list").replaceChildren(...this.crops.map((item, index) =>
      new Option(`${index + 1}. ${item.name || "Untitled"}${item.selected ? "" : " (excluded)"}`, String(index))));
    this.$("crop-list").value = String(this.active);
    this.$("crop-fields").hidden = !crop;
    this.$("delete-crop").disabled = !crop;
    this.$("generate-crop").disabled = !crop;
    this.$("generate-sheet").disabled = !this.crops.some((item) => item.selected);
    this.$("add-crop").disabled = !this.sheet || this.crops.length >= this.sheet.max_crops;
    this.$("redetect-sheet").disabled = !this.sheet;
    if (crop) {
      for (const key of ["name", "x", "y", "width", "height"]) this.$(`crop-${key}`).value = crop[key];
      this.$("crop-size").value = Number(crop.inches.toFixed(3));
      this.$("crop-selected").checked = crop.selected;
    }
    this.drawBoxes();
    this.drawPreview();
  }

  drawBoxes() {
    this.$("crop-boxes").replaceChildren(...this.crops.map((crop, index) => {
      const box = document.createElement("button");
      box.type = "button";
      box.className = `crop-box${index === this.active ? " active" : ""}${crop.selected ? "" : " excluded"}`;
      box.dataset.index = index;
      box.setAttribute("aria-label", `Edit crop ${index + 1}: ${crop.name}`);
      Object.assign(box.style, {
        left: `${crop.x / this.sheet.width * 100}%`, top: `${crop.y / this.sheet.height * 100}%`,
        width: `${crop.width / this.sheet.width * 100}%`, height: `${crop.height / this.sheet.height * 100}%`,
      });
      const label = document.createElement("span");
      label.className = "crop-label";
      label.textContent = index + 1;
      box.append(label);
      for (const corner of ["nw", "ne", "sw", "se"]) {
        const handle = document.createElement("span");
        handle.className = `crop-handle ${corner}`;
        handle.dataset.corner = corner;
        box.append(handle);
      }
      box.addEventListener("click", () => {
        this.active = index;
        this.render();
      });
      return box;
    }));
  }

  drawPreview() {
    const crop = this.crops[this.active];
    const image = this.$("sheet-image");
    if (!crop || !image.complete || !image.naturalWidth) return;
    const canvas = this.$("crop-preview");
    const scale = Math.min(300 / crop.width, 230 / crop.height, 1);
    canvas.width = Math.max(1, Math.round(crop.width * scale));
    canvas.height = Math.max(1, Math.round(crop.height * scale));
    canvas.getContext("2d").drawImage(image, crop.x, crop.y, crop.width, crop.height, 0, 0, canvas.width, canvas.height);
  }

  pointerDown(event) {
    const box = event.target.closest(".crop-box");
    if (!box || this.locked || event.button !== 0) return;
    event.preventDefault();
    this.active = Number(box.dataset.index);
    this.drag = {
      x: event.clientX, y: event.clientY, crop: { ...this.crops[this.active] },
      corner: event.target.dataset.corner,
    };
    this.$("sheet-stage").setPointerCapture(event.pointerId);
    this.render();
  }

  pointerMove(event) {
    if (!this.drag || this.locked) return;
    const { crop, corner } = this.drag;
    const bounds = this.$("sheet-stage").getBoundingClientRect();
    const dx = Math.round((event.clientX - this.drag.x) * this.sheet.width / bounds.width);
    const dy = Math.round((event.clientY - this.drag.y) * this.sheet.height / bounds.height);
    const clamp = (n, low, high) => Math.max(low, Math.min(high, n));
    const next = this.crops[this.active];
    if (!corner) {
      next.x = clamp(crop.x + dx, 0, this.sheet.width - crop.width);
      next.y = clamp(crop.y + dy, 0, this.sheet.height - crop.height);
    } else {
      const left = corner.includes("w") ? clamp(crop.x + dx, 0, crop.x + crop.width - 3) : crop.x;
      const top = corner.includes("n") ? clamp(crop.y + dy, 0, crop.y + crop.height - 3) : crop.y;
      const right = corner.includes("e") ? clamp(crop.x + crop.width + dx, crop.x + 3, this.sheet.width) : crop.x + crop.width;
      const bottom = corner.includes("s") ? clamp(crop.y + crop.height + dy, crop.y + 3, this.sheet.height) : crop.y + crop.height;
      Object.assign(next, { x: left, y: top, width: right - left, height: bottom - top });
    }
    this.render();
  }

  async generate(selected) {
    if (!this.sheet || this.locked) return;
    const crops = selected ? this.crops.filter((crop) => crop.selected) : [this.crops[this.active]];
    if (!crops.length || !crops[0]) return;
    this.busy(true);
    this.results = [];
    this.$("sheet-results").replaceChildren();
    this.$("download-sheet").hidden = true;
    this.$("bundle-sheet").hidden = true;
    let failures = 0;
    try {
      for (const [index, crop] of crops.entries()) {
        this.message(`Generating ${index + 1} of ${crops.length}: ${crop.name || "Untitled"}...`);
        const row = document.createElement("div");
        row.className = "sheet-result";
        this.$("sheet-results").append(row);
        try {
          const data = this.settings();
          data.set("sheet_id", this.sheet.id);
          data.set("crop", JSON.stringify({ x: crop.x, y: crop.y, width: crop.width, height: crop.height }));
          data.set("name", crop.name || `Design-${index + 1}`);
          data.set("size_mm", String(crop.inches * 25.4));
          const result = await this.json(await fetch("/api/generate", { method: "POST", body: data }));
          this.results.push(result);
          const title = document.createElement("strong");
          title.textContent = `${crop.name || "Untitled"} (${Number(crop.inches.toFixed(2))} in)`;
          row.append(title);
          const preview = document.createElement("button");
          preview.type = "button";
          preview.textContent = "Preview / Bambuddy";
          preview.addEventListener("click", () => {
            if (!this.locked) {
              this.showResult(result);
              this.$("preview-heading").scrollIntoView({ behavior: "smooth", block: "start" });
            }
          });
          row.append(preview);
          for (const kind of ["cutter", "stamp", "combined"]) {
            const link = document.createElement("a");
            link.href = result.tools[kind].url;
            link.textContent = kind === "combined" ? "Pair STL" : `${kind} STL`;
            row.append(link);
          }
          const zip = document.createElement("a");
          zip.href = `/api/designs/${result.id}/bundle`;
          zip.textContent = "Design ZIP";
          row.append(zip);
        } catch (error) {
          failures++;
          row.classList.add("error");
          row.textContent = `${crop.name || "Untitled"}: ${error.message}`;
        }
      }
      this.$("bundle-sheet").hidden = this.results.length === 0;
      this.message(`Finished: ${this.results.length} succeeded, ${failures} failed. Successful files remain available below.`, failures > 0);
      if (this.results.length) this.showResult(this.results[0]);
    } finally {
      this.busy(false);
    }
  }

  async bundle() {
    if (this.locked || !this.results.length) return;
    this.busy(true);
    this.message("Building the bundle ZIP...");
    try {
      const result = await this.json(await fetch("/api/bundles", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ design_ids: this.results.map((result) => result.id) }),
      }));
      this.$("download-sheet").href = result.url;
      this.$("download-sheet").hidden = false;
      this.message("Bundle ZIP ready. Each design has its own folder, individual STLs, combined STL and measurements.");
    } catch (error) {
      this.message(error.message, true);
    } finally {
      this.busy(false);
    }
  }
}
