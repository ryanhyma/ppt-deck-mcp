// Deck Studio — MCP App view.
// The View never touches the filesystem: it receives slide URLs in the tool result and
// loads them from the same MCP server origin (declared in the resource's CSP).
import { App } from "./sdk/ext-apps.js";

const app = new App({ name: "Deck Studio", version: "1.0.0" }, {});

const titleEl = document.getElementById("title");
const countEl = document.getElementById("count");
const stage = document.getElementById("stage");
const styleRow = document.createElement("div");
styleRow.className = "styles";
stage.before(styleRow);
const foot = document.getElementById("foot");
const lightbox = document.getElementById("lightbox");
const editor = document.getElementById("editor");
const editorInput = document.getElementById("edit-input");
const statusEl = document.getElementById("status");
let editTarget = null;
let currentFile = "";
const lightboxImg = lightbox.querySelector("img");

lightbox.addEventListener("click", () => lightbox.classList.remove("open"));

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

function openEditor(slide, file) {
  editTarget = slide;
  currentFile = file;
  editor.classList.add("open");
  editorInput.value = "";
  editorInput.placeholder = `What should change on slide ${slide.index}?`;
  editorInput.focus();
}

document.getElementById("edit-cancel").addEventListener("click", () => {
  editor.classList.remove("open");
  editTarget = null;
});

document.getElementById("edit-send").addEventListener("click", async () => {
  const text = editorInput.value.trim();
  if (!text) return;
  const message = editTarget
    ? `Edit ${currentFile}: slide ${editTarget.index} — ${text}. Keep every other slide exactly as it is.`
    : text;
  try {
    await app.sendMessage({ role: "user", content: [{ type: "text", text: message }] });
    statusEl.textContent = "Sent to the agent — watch the chat for the updated deck.";
    editor.classList.remove("open");
    editTarget = null;
  } catch (err) {
    statusEl.textContent = "The host did not accept the message.";
    console.error(err);
  }
});

function renderStyles(templates) {
  if (!Array.isArray(templates) || templates.length === 0) return;
  styleRow.innerHTML = "";
  const head = document.createElement("div");
  head.className = "styles-head";
  head.textContent = "Styles — pick one, then say what the deck is about";
  styleRow.append(head);
  const row = document.createElement("div");
  row.className = "styles-row";
  for (const t of templates) {
    const card = document.createElement("button");
    card.type = "button";
    card.className = "style-card";
    card.title = t.description || t.label;
    if (t.thumb) {
      const img = document.createElement("img");
      img.src = t.thumb;
      img.alt = t.label;
      img.loading = "lazy";
      card.append(img);
    }
    const name = document.createElement("span");
    name.textContent = t.label;
    card.append(name);
    card.addEventListener("click", () => {
      editor.classList.add("open");
      editorInput.value = `Use template ${t.file} for a 5-slide deck about `;
      editTarget = null;
      editorInput.focus();
      editorInput.setSelectionRange(editorInput.value.length, editorInput.value.length);
      statusEl.textContent = `Style: ${t.label} — finish the sentence and press Ask agent.`;
    });
    row.append(card);
  }
  styleRow.append(row);
}

function render(data) {
  if (!data || !Array.isArray(data.slides) || data.slides.length === 0) return;
  renderStyles(data.templates);
  titleEl.textContent = data.title || "Deck";
  countEl.textContent = `${data.slides.length} slides`;

  stage.innerHTML = "";
  const grid = document.createElement("div");
  grid.className = "grid";

  for (const slide of data.slides) {
    const fig = document.createElement("figure");
    const img = document.createElement("img");
    img.loading = "lazy";
    img.alt = `Slide ${slide.index}`;
    img.src = slide.url;
    img.addEventListener("click", () => {
      lightboxImg.src = slide.url;
      lightbox.classList.add("open");
    });
    const cap = document.createElement("figcaption");
    cap.textContent = slide.label ? `${slide.index}. ${slide.label}` : `Slide ${slide.index}`;

    const editBtn = document.createElement("button");
    editBtn.className = "edit";
    editBtn.type = "button";
    editBtn.textContent = "Ask agent to edit";
    editBtn.addEventListener("click", (e) => {
      e.stopPropagation();
      openEditor(slide, data.file);
    });

    fig.append(img, editBtn, cap);
    grid.append(fig);
  }
  stage.append(grid);
  currentFile = data.file || currentFile;
  foot.innerHTML = data.file ? `Saved to <code>${data.file}</code>` : "";
  if (data.download) {
    const link = document.createElement("button");
    link.type = "button";
    link.className = "download";
    link.textContent = "Download .pptx";
    link.addEventListener("click", async () => {
      try {
        await app.openLink({ url: data.download });
      } catch (err) {
        statusEl.textContent = "The host did not open the link.";
        console.error(err);
      }
    });
    foot.append(" ", link);
  }
}

app.ontoolresult = (params) => render(payload(params));
app.onhostcontextchanged = () => {};

await app.connect();

// The host replays the bound tool result after the handshake, so no extra tool call and no
// approval prompt is needed just to display a deck.
