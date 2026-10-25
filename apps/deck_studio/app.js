// Deck Studio — MCP App view.
// The view never touches the filesystem: it receives slide URLs in the tool result and
// loads them from the same MCP server origin (declared in the resource's CSP).
//
// Accessibility (WCAG 2.2 AA / CAN-ASC-EN 301 549 / AODA):
//   * every action is a native button, usable without hover and by keyboard alone
//   * slide images open a native <dialog> viewer with arrows, Home/End, Esc, Tab trap
//   * focus returns to the control that opened a panel or the viewer
//   * the slide counter is announced through a polite live region
import { App } from "./sdk/ext-apps.js";

const app = new App({ name: "Deck Studio", version: "1.1.0" }, {});

const $ = (id) => document.getElementById(id);
const titleEl = $("title");
const countEl = $("count");
const stage = $("stage");
const stylesSection = $("styles");
const stylesRow = $("styles-row");
const foot = $("foot");
const statusEl = $("status");

const editor = $("editor");
const editorInput = $("edit-input");
const stylePanel = $("stylepanel");

const viewer = $("viewer");
const viewerImg = $("viewer-img");
const viewerTitle = $("viewer-title");
const viewerLive = $("viewer-live");
const viewerPrev = $("viewer-prev");
const viewerNext = $("viewer-next");
const viewerClose = $("viewer-close");

let currentFile = "";
let styleTarget = null;
let styleOpener = null;
let editTarget = null;
let editOpener = null;

let deckSlides = [];      // [{index, label, url}]
let viewerAt = 0;         // index into deckSlides

function setStatus(message) {
  statusEl.textContent = message;
}

function unwrap(data) {
  // FastMCP wraps dict returns as { result: {...} }
  if (data && typeof data === "object" && data.result && typeof data.result === "object"
      && (data.result.slides || data.result.deck_id)) return data.result;
  return data;
}

function payload(params) {
  // params: { content, structuredContent, _meta, isError }
  if (params?.structuredContent) return unwrap(params.structuredContent);
  const text = (params?.content || []).find((c) => c.type === "text")?.text;
  if (!text) return null;
  try { return unwrap(JSON.parse(text)); } catch { return null; }
}

/** Tool results arrive as structuredContent when available, else as JSON text. */
function toolData(res) {
  const structured = unwrap(res?.structuredContent ?? null);
  if (structured) return structured;
  const text = (res?.content || []).find((c) => c.type === "text")?.text || "";
  try { return text ? unwrap(JSON.parse(text)) : null; } catch { return { error: text }; }
}

function refreshSlideImages(slides) {
  const bust = Date.now();
  for (const slide of slides) {
    const img = document.querySelector(`img[data-slide="${slide.index}"]`);
    if (img) img.src = `${slide.url}?v=${bust}`;
  }
}

const slideTitle = (slide) => slide.label || `Slide ${slide.index}`;
const slideAlt = (slide, total) =>
  `Slide ${slide.index} of ${total}: ${slide.label || "untitled"}`;

// ---------------------------------------------------------------- slide viewer
function showSlide(position) {
  if (deckSlides.length === 0) return;
  viewerAt = (position + deckSlides.length) % deckSlides.length;
  const slide = deckSlides[viewerAt];
  viewerImg.src = slide.url;
  viewerImg.alt = slideAlt(slide, deckSlides.length);
  const heading = `${slide.index}. ${slideTitle(slide)} — ${viewerAt + 1} of ${deckSlides.length}`;
  viewerTitle.textContent = heading;
  viewerLive.textContent = heading;          // announce arrow-key moves
  viewerPrev.disabled = deckSlides.length < 2;
  viewerNext.disabled = deckSlides.length < 2;
}

function openViewer(position) {
  showSlide(position);
  if (typeof viewer.showModal === "function") viewer.showModal();
  else viewer.setAttribute("open", "");      // very old engines: at least visible
  viewerClose.focus();
}

function closeViewer() {
  if (viewer.open && typeof viewer.close === "function") viewer.close();
  else viewer.removeAttribute("open");
}

viewerClose.addEventListener("click", closeViewer);
viewerPrev.addEventListener("click", () => showSlide(viewerAt - 1));
viewerNext.addEventListener("click", () => showSlide(viewerAt + 1));

viewer.addEventListener("keydown", (event) => {
  const move = { ArrowRight: 1, ArrowLeft: -1, PageDown: 1, PageUp: -1 }[event.key];
  if (move) { event.preventDefault(); showSlide(viewerAt + move); return; }
  if (event.key === "Home") { event.preventDefault(); showSlide(0); return; }
  if (event.key === "End") { event.preventDefault(); showSlide(deckSlides.length - 1); return; }
  if (event.key === "Escape") { event.preventDefault(); closeViewer(); return; }
  // The App lives in an iframe, so the browser's dialog focus trap only covers this
  // document — Tab past the last control would land in the chat behind the modal.
  // Wrap explicitly so keyboard users stay inside the viewer.
  if (event.key === "Tab") {
    const focusable = [...viewer.querySelectorAll("button:not([disabled]), [href], [tabindex]:not([tabindex='-1'])")];
    if (focusable.length === 0) return;
    const first = focusable[0];
    const last = focusable[focusable.length - 1];
    if (event.shiftKey && document.activeElement === first) {
      event.preventDefault();
      last.focus();
    } else if (!event.shiftKey && document.activeElement === last) {
      event.preventDefault();
      first.focus();
    }
  }
});
// Click on the backdrop (outside the dialog box) closes, like every other lightbox.
viewer.addEventListener("click", (event) => {
  if (event.target === viewer) closeViewer();
});

// ---------------------------------------------------------------- style panel
function openStyle(slide, file, opener) {
  styleTarget = slide;
  styleOpener = opener || document.activeElement;
  currentFile = file;
  stylePanel.classList.add("open");
  $("si-target").textContent = `Editing slide ${slide.index} — ${slideTitle(slide)}`;
  for (const id of ["si-title", "si-body", "si-tcolor", "si-bcolor", "si-font", "si-align"]) {
    $(id).value = "";
  }
  $("si-title").focus();
}

function closeStyle() {
  stylePanel.classList.remove("open");
  styleTarget = null;
  if (styleOpener && document.contains(styleOpener)) styleOpener.focus();
  styleOpener = null;
}

// ---------------------------------------------------------------- edit panel
function openEditor(slide, file, opener) {
  editTarget = slide;
  editOpener = opener || document.activeElement;
  currentFile = file;
  editor.classList.add("open");
  editorInput.value = "";
  editorInput.placeholder = slide
    ? `What should change on slide ${slide.index}?`
    : "Describe the deck you want…";
  editorInput.focus();
}

function closeEditor() {
  editor.classList.remove("open");
  editTarget = null;
  if (editOpener && document.contains(editOpener)) editOpener.focus();
  editOpener = null;
}

const on = (id, event, fn) => {
  const el = $(id);
  if (el) el.addEventListener(event, fn);
  else console.warn("missing control", id);
};

on("st-clear", "click", () => {
  for (const id of ["st-phrase", "st-color"]) { const el = $(id); if (el) el.value = ""; }
  const st = $("st-style"); if (st) st.value = "";
});

on("st-apply", "click", async () => {
  if (!styleTarget) return;
  const phrase = ($("st-phrase")?.value || "").trim();
  if (!phrase) { setStatus("Type the phrase to emphasise first."); $("st-phrase").focus(); return; }
  const args = { file: currentFile, slide_index: styleTarget.index, phrase };
  const colour = ($("st-color")?.value || "").trim();
  const emphasis = $("st-style")?.value || "";
  if (colour) args.color = colour;
  if (emphasis) args[emphasis] = true;
  if (!args.color && !emphasis) { setStatus("Pick a colour or an emphasis."); return; }
  try {
    const data = toolData(await app.callServerTool({ name: "style_text", arguments: args }));
    if (data?.slides) {
      refreshSlideImages(data.slides);
      setStatus(`Emphasised "${data.phrase}" on slide ${data.slide_index} (${data.runs_changed} run${data.runs_changed > 1 ? "s" : ""}).`);
    } else {
      setStatus(data?.error ? String(data.error).slice(0, 160) : "The tool returned no slide data.");
    }
  } catch (err) {
    setStatus("Phrase styling failed: " + String(err).slice(0, 90));
  }
});

on("si-cancel", "click", closeStyle);

on("si-apply", "click", async () => {
  if (!styleTarget) return;
  const args = { file: currentFile, slide_index: styleTarget.index };
  const title = $("si-title").value.trim();
  const body = $("si-body").value.trim();
  const font = $("si-font").value;
  const tcolor = $("si-tcolor").value.trim();
  const bcolor = $("si-bcolor").value.trim();
  const align = $("si-align").value;
  if (title) args.title_size = Number(title);
  if (body) args.body_size = Number(body);
  if (font) args.font = font;
  if (tcolor) args.title_color = tcolor;
  if (bcolor) args.body_color = bcolor;
  if (align) args.align = align;
  if (Object.keys(args).length <= 2) { setStatus("Pick at least one style change."); return; }
  try {
    const data = toolData(await app.callServerTool({ name: "style_slide", arguments: args }));
    if (data?.slides) {
      refreshSlideImages(data.slides);
      setStatus(`Slide ${data.slide_index} restyled (${(data.applied || []).join(", ")}).`);
      closeStyle();
    } else {
      setStatus(data?.error ? String(data.error).slice(0, 160) : "The tool returned no slide data.");
    }
  } catch (err) {
    setStatus("Style change failed: " + String(err).slice(0, 90));
    console.error(err);
  }
});

on("edit-cancel", "click", closeEditor);

on("edit-send", "click", async () => {
  const text = editorInput.value.trim();
  if (!text) { setStatus("Type what should change first."); editorInput.focus(); return; }
  const message = editTarget
    ? `Edit ${currentFile}: slide ${editTarget.index} — ${text}. Keep every other slide exactly as it is.`
    : text;
  try {
    await app.sendMessage({ role: "user", content: [{ type: "text", text: message }] });
    setStatus("Sent to the agent — watch the chat for the updated deck.");
    closeEditor();
  } catch (err) {
    setStatus("The host did not accept the message.");
    console.error(err);
  }
});

// ---------------------------------------------------------------- rendering
function renderStyles(templates) {
  if (!Array.isArray(templates) || templates.length === 0) {
    stylesSection.hidden = true;
    return;
  }
  stylesRow.innerHTML = "";
  stylesSection.hidden = false;
  for (const t of templates) {
    const card = document.createElement("button");
    card.type = "button";
    card.className = "style-card";
    card.title = t.description || t.label;
    card.setAttribute("aria-label", `Use the ${t.label} style`);
    if (t.thumb) {
      const img = document.createElement("img");
      img.src = t.thumb;
      img.alt = "";                 // decorative: the button carries the name
      img.loading = "lazy";
      card.append(img);
    }
    const name = document.createElement("span");
    name.textContent = t.label;
    card.append(name);
    card.addEventListener("click", (event) => {
      editor.classList.add("open");
      editOpener = event.currentTarget;
      editTarget = null;
      editorInput.value = `Use template ${t.file} for a 5-slide deck about `;
      editorInput.focus();
      editorInput.setSelectionRange(editorInput.value.length, editorInput.value.length);
      setStatus(`Style: ${t.label} — finish the sentence and press Ask agent.`);
    });
    stylesRow.append(card);
  }
}

function slideCard(slide, total, file) {
  const fig = document.createElement("figure");

  const thumb = document.createElement("button");
  thumb.type = "button";
  thumb.className = "thumb";
  thumb.setAttribute("aria-label", `View slide ${slide.index} larger: ${slideTitle(slide)}`);
  const img = document.createElement("img");
  img.loading = "lazy";
  img.decoding = "async";
  img.alt = "";                     // the button label already names the slide
  img.src = slide.url;
  img.dataset.slide = String(slide.index);
  thumb.append(img);
  thumb.addEventListener("click", () => openViewer(deckSlides.indexOf(slide)));

  const cap = document.createElement("figcaption");
  const num = document.createElement("span");
  num.className = "cap-num";
  num.textContent = String(slide.index);
  const label = document.createElement("span");
  label.className = "cap-label";
  label.textContent = slide.label || "Untitled slide";
  label.title = label.textContent;
  cap.append(num, label);

  const actions = document.createElement("div");
  actions.className = "card-actions";

  const styleBtn = document.createElement("button");
  styleBtn.type = "button";
  styleBtn.className = "btn";
  styleBtn.textContent = "Style…";
  styleBtn.setAttribute("aria-label", `Format slide ${slide.index}: ${slideTitle(slide)}`);
  styleBtn.addEventListener("click", (event) => openStyle(slide, file, event.currentTarget));

  const editBtn = document.createElement("button");
  editBtn.type = "button";
  editBtn.className = "btn";
  editBtn.textContent = "Ask agent…";
  editBtn.setAttribute("aria-label", `Ask the agent to edit slide ${slide.index}: ${slideTitle(slide)}`);
  editBtn.addEventListener("click", (event) => openEditor(slide, file, event.currentTarget));

  actions.append(styleBtn, editBtn);
  fig.append(thumb, cap, actions);
  fig.dataset.slideCard = String(slide.index);
  return fig;
}

function render(data) {
  if (!data || !Array.isArray(data.slides) || data.slides.length === 0) return;
  renderStyles(data.templates);
  titleEl.textContent = data.title || "Deck";
  countEl.textContent = `${data.slides.length} slide${data.slides.length === 1 ? "" : "s"}`;

  deckSlides = data.slides;
  stage.innerHTML = "";
  const grid = document.createElement("div");
  grid.className = "grid";
  for (const slide of deckSlides) grid.append(slideCard(slide, deckSlides.length, data.file));
  stage.append(grid);

  currentFile = data.file || currentFile;
  foot.innerHTML = data.file ? `Saved to <code>${data.file}</code>` : "";
  if (data.download) {
    const link = document.createElement("button");
    link.type = "button";
    link.className = "btn download";
    link.textContent = "Download .pptx";
    link.setAttribute("aria-label", "Download the PowerPoint file");
    link.addEventListener("click", async () => {
      try { await app.openLink({ url: data.download }); }
      catch (err) { setStatus("The host did not open the link."); console.error(err); }
    });
    foot.append(" ", link);
  }
}

app.ontoolresult = (params) => render(payload(params));
app.onhostcontextchanged = () => {};

await app.connect();

// The host replays the bound tool result after the handshake, so no extra tool call and no
// approval prompt is needed just to display a deck.
