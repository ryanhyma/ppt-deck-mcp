// Deck Studio — MCP App view.
// The View never touches the filesystem: it receives slide URLs in the tool result and
// loads them from the same MCP server origin (declared in the resource's CSP).
import { App } from "./sdk/ext-apps.js";

const app = new App({ name: "Deck Studio", version: "1.0.0" }, {});

const titleEl = document.getElementById("title");
const countEl = document.getElementById("count");
const stage = document.getElementById("stage");
const foot = document.getElementById("foot");
const lightbox = document.getElementById("lightbox");
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

function render(data) {
  if (!data || !Array.isArray(data.slides) || data.slides.length === 0) return;
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
    fig.append(img, cap);
    grid.append(fig);
  }
  stage.append(grid);
  foot.innerHTML = data.file ? `Saved to <code>${data.file}</code>` : "";
}

app.ontoolresult = (params) => render(payload(params));
app.onhostcontextchanged = () => {};

await app.connect();

// The host replays the bound tool result after the handshake, so no extra tool call and no
// approval prompt is needed just to display a deck.
