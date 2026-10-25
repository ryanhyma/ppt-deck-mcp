// Deck Studio — MCP App view.
// The view never touches the filesystem: it receives slide URLs in the tool result and
// loads them from the same MCP server origin (declared in the resource's CSP).
//
// Accessibility (WCAG 2.2 AA / CAN-ASC-EN 301 549 / AODA):
//   * every action is a native button, usable without hover and by keyboard alone
//   * slide images open a native <dialog> viewer with arrows, Home/End, Esc, Tab trap
//   * focus returns to the control that opened a panel or the viewer
//   * the slide counter is announced through a polite live region
//   * English and Canadian French, with the active language reflected on <html lang>
import { App } from "./sdk/ext-apps.js";

const app = new App({ name: "Deck Studio", version: "1.2.0" }, {});

// ---------------------------------------------------------------- strings (en-CA / fr-CA)
const STRINGS = {
  en: {
    waiting: "waiting for a tool result",
    lede: "Slides below are live images of the latest deck. Open one to view it larger.",
    stylesHead: "Styles — pick one, then say what the deck is about",
    emptyTitle: "No deck yet",
    emptyBody: "Build a deck (or call preview_deck) and its slides appear here.",
    slides_one: "1 slide",
    slides_many: "{n} slides",
    viewSlide: "View slide {n} larger: {title}",
    styleSlide: "Format slide {n}: {title}",
    askSlide: "Ask the agent to edit slide {n}: {title}",
    styleBtn: "Style…",
    askBtn: "Ask agent…",
    untitled: "Untitled slide",
    viewerTitle: "{n}. {title} — {i} of {total}",
    close: "Close",
    prev: "Previous slide",
    next: "Next slide",
    viewerHint: "Arrow keys move between slides · Home / End jump to the ends · Esc closes.",
    download: "Download .pptx",
    downloadAria: "Download the PowerPoint file",
    savedTo: "Saved to",
    stylepanelHead: "Slide formatting",
    siTitle: "Title size (pt)",
    siBody: "Body size (pt)",
    siFont: "Font",
    siTColor: "Title colour",
    siBColor: "Body colour",
    siAlign: "Alignment",
    stPhrase: "Emphasise a phrase",
    stColor: "Phrase colour",
    stEmphasis: "Emphasis",
    optBold: "bold",
    optItalic: "italic",
    optUnderline: "underline",
    alignLeft: "left",
    alignCenter: "center",
    alignRight: "right",
    stApply: "Apply to phrase",
    stClear: "Clear phrase",
    stHint: "one run only — keep the phrase short",
    siApply: "Apply to slide",
    cancel: "Cancel",
    editorHead: "Ask the agent to edit this slide",
    editLabel: "Change to request for this slide",
    editPlaceholder: "What should change on this slide?",
    editPlaceholderDeck: "Describe the deck you want…",
    editSend: "Ask agent",
    editingSlide: "Editing slide {n} — {title}",
    useStyle: "Use the {label} style",
    phraseFirst: "Type the phrase to emphasise first.",
    pickColour: "Pick a colour or an emphasis.",
    phraseDone: 'Emphasised "{phrase}" on slide {n} ({runs} run(s)).',
    styleDone: "Slide {n} restyled ({applied}).",
    noSlideData: "The tool returned no slide data.",
    pickStyle: "Pick at least one style change.",
    phraseFailed: "Phrase styling failed: {err}",
    styleFailed: "Style change failed: {err}",
    pickTemplate: "Style: {label} — finish the sentence and press Ask agent.",
    sentAgent: "Sent to the agent — watch the chat for the updated deck.",
    typeChange: "Type what should change first.",
    hostRejected: "The host did not accept the message.",
    hostLink: "The host did not open the link.",
    untitledDeck: "Deck Studio",
  },
  fr: {
    waiting: "en attente d'un résultat d'outil",
    lede: "Les diapositives ci-dessous sont les images du diaporama le plus récent. Ouvrez-en une pour l'agrandir.",
    stylesHead: "Styles — choisissez-en un, puis précisez le sujet du diaporama",
    emptyTitle: "Aucun diaporama pour l'instant",
    emptyBody: "Créez un diaporama (ou appelez preview_deck) : ses diapositives s'afficheront ici.",
    slides_one: "1 diapositive",
    slides_many: "{n} diapositives",
    viewSlide: "Agrandir la diapositive {n} : {title}",
    styleSlide: "Mettre en forme la diapositive {n} : {title}",
    askSlide: "Demander à l'agent de modifier la diapositive {n} : {title}",
    styleBtn: "Style…",
    askBtn: "Demander à l'agent…",
    untitled: "Diapositive sans titre",
    viewerTitle: "{n}. {title} — {i} sur {total}",
    close: "Fermer",
    prev: "Diapositive précédente",
    next: "Diapositive suivante",
    viewerHint: "Les flèches changent de diapositive · Début et Fin vont aux extrémités · Échap ferme.",
    download: "Télécharger le .pptx",
    downloadAria: "Télécharger le fichier PowerPoint",
    savedTo: "Enregistré dans",
    stylepanelHead: "Mise en forme de la diapositive",
    siTitle: "Taille du titre (pt)",
    siBody: "Taille du texte (pt)",
    siFont: "Police",
    siTColor: "Couleur du titre",
    siBColor: "Couleur du texte",
    siAlign: "Alignement",
    stPhrase: "Mettre en évidence une expression",
    stColor: "Couleur de l'expression",
    stEmphasis: "Emphase",
    optBold: "gras",
    optItalic: "italique",
    optUnderline: "souligné",
    alignLeft: "gauche",
    alignCenter: "centré",
    alignRight: "droite",
    stApply: "Appliquer à l'expression",
    stClear: "Effacer l'expression",
    stHint: "une seule séquence — gardez l'expression courte",
    siApply: "Appliquer à la diapositive",
    cancel: "Annuler",
    editorHead: "Demander à l'agent de modifier cette diapositive",
    editLabel: "Modification à demander pour cette diapositive",
    editPlaceholder: "Que faut-il changer sur cette diapositive ?",
    editPlaceholderDeck: "Décrivez le diaporama souhaité…",
    editSend: "Demander à l'agent",
    editingSlide: "Modification de la diapositive {n} — {title}",
    useStyle: "Utiliser le style {label}",
    phraseFirst: "Saisissez d'abord l'expression à mettre en évidence.",
    pickColour: "Choisissez une couleur ou une emphase.",
    phraseDone: "« {phrase} » mis en évidence à la diapositive {n} ({runs} segment(s)).",
    styleDone: "Diapositive {n} remise en forme ({applied}).",
    noSlideData: "L'outil n'a renvoyé aucune diapositive.",
    pickStyle: "Choisissez au moins une modification de style.",
    phraseFailed: "Échec de la mise en évidence : {err}",
    styleFailed: "Échec de la modification de style : {err}",
    pickTemplate: "Style : {label} — complétez la phrase puis appuyez sur Demander à l'agent.",
    sentAgent: "Envoyé à l'agent — surveillez la conversation pour le diaporama mis à jour.",
    typeChange: "Saisissez d'abord la modification souhaitée.",
    hostRejected: "L'hôte a refusé le message.",
    hostLink: "L'hôte n'a pas ouvert le lien.",
    untitledDeck: "Studio de diaporamas",
  },
};

const $ = (id) => document.getElementById(id);

const titleEl = $("title");
const countEl = $("count");
const stage = $("stage");
const stylesSection = $("styles");
const stylesRow = $("styles-row");
const toolbar = $("toolbar");
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

const langButtons = { en: $("lang-en"), fr: $("lang-fr") };

let lang = "en";
let langChosenByUser = false;
let lastDeck = null;
let statusState = null;   // {key, vars} so the message re-renders in the new language

let currentFile = "";
let styleTarget = null;
let styleOpener = null;
let editTarget = null;
let editOpener = null;

let deckSlides = [];      // [{index, label, url}]
let viewerAt = 0;         // index into deckSlides

function t(key, vars) {
  const table = STRINGS[lang] || STRINGS.en;
  const raw = table[key] ?? STRINGS.en[key] ?? key;
  if (!vars) return raw;
  return raw.replace(/\{(\w+)\}/g, (whole, name) =>
    Object.prototype.hasOwnProperty.call(vars, name) ? String(vars[name]) : whole);
}

function setStatus(key, vars) {
  statusState = key ? { key, vars } : null;
  statusEl.textContent = key ? t(key, vars) : "";
}

/** Server-supplied wording (tool errors) is shown verbatim and is not re-translated. */
function setStatusText(text) {
  statusState = null;
  statusEl.textContent = String(text || "").slice(0, 200);
}

// ---------------------------------------------------------------- language
function applyStaticText() {
  document.documentElement.lang = lang === "fr" ? "fr-CA" : "en-CA";
  for (const el of document.querySelectorAll("[data-i18n]")) el.textContent = t(el.dataset.i18n);
  for (const el of document.querySelectorAll("[data-i18n-aria]")) {
    el.setAttribute("aria-label", t(el.dataset.i18nAria));
  }
  for (const el of document.querySelectorAll("[data-i18n-placeholder]")) {
    el.setAttribute("placeholder", t(el.dataset.i18nPlaceholder));
  }
  for (const [code, button] of Object.entries(langButtons)) {
    if (button) button.setAttribute("aria-pressed", String(code === lang));
  }
  if (statusState) statusEl.textContent = t(statusState.key, statusState.vars);
  if (!lastDeck) {
    countEl.textContent = t("waiting");
    titleEl.textContent = t("untitledDeck");
  }
  if (styleTarget) {
    $("si-target").textContent = t("editingSlide", { n: styleTarget.index, title: slideTitle(styleTarget) });
  }
}

function setLang(next) {
  if (!STRINGS[next] || next === lang) return;
  lang = next;
  applyStaticText();
  if (lastDeck) render(lastDeck);          // relabel the grid in the new language
}

function pickInitialLang() {
  let hostLocale = "";
  try { hostLocale = app.getHostContext?.()?.locale || ""; } catch { /* not connected yet */ }
  const candidates = [hostLocale, ...(navigator.languages || []), navigator.language || ""];
  for (const value of candidates) {
    const tag = String(value || "").toLowerCase();
    if (tag.startsWith("fr")) return "fr";
    if (tag.startsWith("en")) return "en";
  }
  return "en";
}

for (const [code, button] of Object.entries(langButtons)) {
  if (!button) continue;
  button.addEventListener("click", () => {
    langChosenByUser = true;
    setLang(code);
  });
}

// ---------------------------------------------------------------- tool plumbing
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

const slideTitle = (slide) => slide.label || t("untitled");

// ---------------------------------------------------------------- slide viewer
function showSlide(position) {
  if (deckSlides.length === 0) return;
  viewerAt = (position + deckSlides.length) % deckSlides.length;
  const slide = deckSlides[viewerAt];
  viewerImg.src = slide.url;
  viewerImg.alt = `${t("viewSlide", { n: slide.index, title: slide.label || t("untitled") })}`;
  const heading = t("viewerTitle", {
    n: slide.index,
    title: slide.label || t("untitled"),
    i: viewerAt + 1,
    total: deckSlides.length,
  });
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
  $("si-target").textContent = t("editingSlide", { n: slide.index, title: slideTitle(slide) });
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
    ? t("editPlaceholder", { n: slide.index })
    : t("editPlaceholderDeck");
  editorInput.setAttribute("aria-label", slide
    ? t("editLabel") + ` — ${slide.index}`
    : t("editLabel"));
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
  if (!phrase) { setStatus("phraseFirst"); $("st-phrase").focus(); return; }
  const args = { file: currentFile, slide_index: styleTarget.index, phrase };
  const colour = ($("st-color")?.value || "").trim();
  const emphasis = $("st-style")?.value || "";
  if (colour) args.color = colour;
  if (emphasis) args[emphasis] = true;
  if (!args.color && !emphasis) { setStatus("pickColour"); return; }
  try {
    const data = toolData(await app.callServerTool({ name: "style_text", arguments: args }));
    if (data?.slides) {
      refreshSlideImages(data.slides);
      setStatus("phraseDone", {
        phrase: data.phrase, n: data.slide_index, runs: data.runs_changed,
      });
    } else if (data?.error) {
      setStatusText(data.error);
    } else {
      setStatus("noSlideData");
    }
  } catch (err) {
    setStatus("phraseFailed", { err: String(err).slice(0, 90) });
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
  if (Object.keys(args).length <= 2) { setStatus("pickStyle"); return; }
  try {
    const data = toolData(await app.callServerTool({ name: "style_slide", arguments: args }));
    if (data?.slides) {
      refreshSlideImages(data.slides);
      setStatus("styleDone", { n: data.slide_index, applied: (data.applied || []).join(", ") });
      closeStyle();
    } else if (data?.error) {
      setStatusText(data.error);
    } else {
      setStatus("noSlideData");
    }
  } catch (err) {
    setStatus("styleFailed", { err: String(err).slice(0, 90) });
    console.error(err);
  }
});

on("edit-cancel", "click", closeEditor);

on("edit-send", "click", async () => {
  const text = editorInput.value.trim();
  if (!text) { setStatus("typeChange"); editorInput.focus(); return; }
  const message = editTarget
    ? `Edit ${currentFile}: slide ${editTarget.index} — ${text}. Keep every other slide exactly as it is.`
    : text;
  try {
    await app.sendMessage({ role: "user", content: [{ type: "text", text: message }] });
    setStatus("sentAgent");
    closeEditor();
  } catch (err) {
    setStatus("hostRejected");
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
  for (const tpl of templates) {
    const card = document.createElement("button");
    card.type = "button";
    card.className = "style-card";
    card.title = tpl.description || tpl.label;
    card.setAttribute("aria-label", t("useStyle", { label: tpl.label }));
    if (tpl.thumb) {
      const img = document.createElement("img");
      img.src = tpl.thumb;
      img.alt = "";                 // decorative: the button carries the name
      img.loading = "lazy";
      card.append(img);
    }
    const name = document.createElement("span");
    name.textContent = tpl.label;
    card.append(name);
    card.addEventListener("click", (event) => {
      editor.classList.add("open");
      editOpener = event.currentTarget;
      editTarget = null;
      editorInput.value = `Use template ${tpl.file} for a 5-slide deck about `;
      editorInput.focus();
      editorInput.setSelectionRange(editorInput.value.length, editorInput.value.length);
      setStatus("pickTemplate", { label: tpl.label });
    });
    stylesRow.append(card);
  }
}

function slideCard(slide, file) {
  const fig = document.createElement("figure");

  const thumb = document.createElement("button");
  thumb.type = "button";
  thumb.className = "thumb";
  thumb.setAttribute("aria-label", t("viewSlide", { n: slide.index, title: slideTitle(slide) }));
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
  label.textContent = slide.label || t("untitled");
  label.title = label.textContent;
  cap.append(num, label);

  const actions = document.createElement("div");
  actions.className = "card-actions";

  const styleBtn = document.createElement("button");
  styleBtn.type = "button";
  styleBtn.className = "btn";
  styleBtn.textContent = t("styleBtn");
  styleBtn.setAttribute("aria-label", t("styleSlide", { n: slide.index, title: slideTitle(slide) }));
  styleBtn.addEventListener("click", (event) => openStyle(slide, file, event.currentTarget));

  const editBtn = document.createElement("button");
  editBtn.type = "button";
  editBtn.className = "btn";
  editBtn.textContent = t("askBtn");
  editBtn.setAttribute("aria-label", t("askSlide", { n: slide.index, title: slideTitle(slide) }));
  editBtn.addEventListener("click", (event) => openEditor(slide, file, event.currentTarget));

  actions.append(styleBtn, editBtn);
  fig.append(thumb, cap, actions);
  fig.dataset.slideCard = String(slide.index);
  return fig;
}

function renderToolbar(data) {
  toolbar.innerHTML = "";
  if (data.download) {
    const link = document.createElement("button");
    link.type = "button";
    link.className = "btn primary download";
    link.textContent = t("download");
    link.setAttribute("aria-label", t("downloadAria"));
    link.addEventListener("click", async () => {
      try { await app.openLink({ url: data.download }); }
      catch (err) { setStatus("hostLink"); console.error(err); }
    });
    toolbar.append(link);
  }
}

function render(data) {
  if (!data || !Array.isArray(data.slides) || data.slides.length === 0) return;
  lastDeck = data;
  renderStyles(data.templates);
  titleEl.textContent = data.title || t("untitledDeck");
  const n = data.slides.length;
  countEl.textContent = n === 1 ? t("slides_one") : t("slides_many", { n });

  deckSlides = data.slides;
  stage.innerHTML = "";
  const grid = document.createElement("div");
  grid.className = "grid";
  for (const slide of deckSlides) grid.append(slideCard(slide, data.file));
  stage.append(grid);

  currentFile = data.file || currentFile;
  renderToolbar(data);
  foot.innerHTML = data.file ? `${t("savedTo")} <code>${data.file}</code>` : "";
}

app.ontoolresult = (params) => render(payload(params));
app.onhostcontextchanged = (context) => {
  // Follow the host's language until the user picks one themselves.
  if (langChosenByUser) return;
  const tag = String(context?.locale || "").toLowerCase();
  if (tag.startsWith("fr")) setLang("fr");
  else if (tag.startsWith("en")) setLang("en");
};

// Paint the chrome before (or without) the handshake: a pane opened outside LibreChat
// should still show its empty state in the right language instead of a blank frame.
setLang(pickInitialLang());
applyStaticText();

// Test seam (dev only): dev-preview.html injects a fixture tool result so layout, keyboard
// behaviour and the language switch can be checked without spending a model turn. Nothing
// in normal use sets this global, and the host still replays the real result via
// ontoolresult after the handshake.
if (globalThis.__DECK_MOCK__) {
  render(globalThis.__DECK_MOCK__);
} else {
  try {
    await app.connect();
  } catch (err) {
    console.warn("[deck-studio] host bridge unavailable:", err);
  }
}
