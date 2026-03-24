/**
 * popup.js – Chrome Extension popup for the Web Scraping Agent
 *
 * Flow:
 *  1. On load, reads the active tab URL and shows it.
 *  2. User picks a focus and clicks "Extract content".
 *  3. POST { url, focus } to the local server at http://127.0.0.1:7331/scrape.
 *  4. Render the returned JSON (speakers, topics, dates, title).
 *  5. "Copy full JSON" copies the raw result to the clipboard.
 */

const SERVER_URL = "http://127.0.0.1:7331";

// ── DOM refs ──────────────────────────────────────────────────────────────
const urlDisplay    = document.getElementById("urlDisplay");
const focusSelect   = document.getElementById("focusSelect");
const scrapeBtn     = document.getElementById("scrapeBtn");
const status        = document.getElementById("status");
const resultArea    = document.getElementById("resultArea");
const copyBtn       = document.getElementById("copyBtn");

const titleSection    = document.getElementById("titleSection");
const titleValue      = document.getElementById("titleValue");
const speakersSection = document.getElementById("speakersSection");
const speakersList    = document.getElementById("speakersList");
const topicsSection   = document.getElementById("topicsSection");
const topicsList      = document.getElementById("topicsList");
const datesSection    = document.getElementById("datesSection");
const datesValue      = document.getElementById("datesValue");

let currentUrl = "";
let lastResult = null;

// ── Initialise ─────────────────────────────────────────────────────────────
chrome.tabs.query({ active: true, currentWindow: true }, (tabs) => {
  const tab = tabs[0];
  if (tab && tab.url) {
    currentUrl = tab.url;
    urlDisplay.textContent = currentUrl;
  } else {
    urlDisplay.textContent = "No URL detected";
    scrapeBtn.disabled = true;
  }
});

// ── Scrape ─────────────────────────────────────────────────────────────────
scrapeBtn.addEventListener("click", async () => {
  if (!currentUrl) return;

  setStatus("loading", "Scraping… this may take 20–60 seconds");
  scrapeBtn.disabled = true;
  resultArea.style.display = "none";
  lastResult = null;

  try {
    // Check server health first
    const health = await fetch(`${SERVER_URL}/health`).catch(() => null);
    if (!health || !health.ok) {
      setStatus("error", "Cannot reach the local server. Is it running?\n  python server.py");
      return;
    }

    const response = await fetch(`${SERVER_URL}/scrape`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ url: currentUrl, focus: focusSelect.value }),
    });

    const data = await response.json();

    if (!response.ok || data.error) {
      setStatus("error", `Error: ${data.error || response.statusText}`);
      return;
    }

    lastResult = data;
    renderResult(data);
    setStatus("success", `Done — ${(data.speakers || []).length} speaker(s) found`);
  } catch (err) {
    setStatus("error", `Request failed: ${err.message}`);
  } finally {
    scrapeBtn.disabled = false;
  }
});

// ── Copy JSON ───────────────────────────────────────────────────────────────
copyBtn.addEventListener("click", () => {
  if (!lastResult) return;
  navigator.clipboard.writeText(JSON.stringify(lastResult, null, 2)).then(() => {
    const orig = copyBtn.textContent;
    copyBtn.textContent = "Copied!";
    setTimeout(() => (copyBtn.textContent = orig), 1500);
  });
});

// ── Render ─────────────────────────────────────────────────────────────────
function renderResult(data) {
  // Title
  if (data.title) {
    titleValue.textContent = data.title;
    titleSection.style.display = "";
  }

  // Speakers
  speakersList.innerHTML = "";
  if (data.speakers && data.speakers.length > 0) {
    data.speakers.forEach((sp) => {
      const card = document.createElement("div");
      card.className = "speaker-card";

      const name = document.createElement("div");
      name.className = "speaker-name";
      name.textContent = sp.name || "Unknown";
      card.appendChild(name);

      const meta = [sp.title, sp.affiliation].filter(Boolean).join(" · ");
      if (meta) {
        const metaEl = document.createElement("div");
        metaEl.className = "speaker-meta";
        metaEl.textContent = meta;
        card.appendChild(metaEl);
      }

      if (sp.bio) {
        const bio = document.createElement("div");
        bio.className = "speaker-meta";
        bio.style.marginTop = "4px";
        bio.style.color = "#6b7280";
        bio.textContent = sp.bio.length > 120 ? sp.bio.slice(0, 120) + "…" : sp.bio;
        card.appendChild(bio);
      }

      speakersList.appendChild(card);
    });
    speakersSection.style.display = "";
  }

  // Topics
  topicsList.innerHTML = "";
  if (data.topics && data.topics.length > 0) {
    data.topics.forEach((topic) => {
      const tag = document.createElement("span");
      tag.className = "tag";
      tag.textContent = topic;
      topicsList.appendChild(tag);
    });
    topicsSection.style.display = "";
  }

  // Dates
  if (data.dates) {
    datesValue.textContent = data.dates;
    datesSection.style.display = "";
  }

  resultArea.style.display = "";
}

// ── Status helper ──────────────────────────────────────────────────────────
function setStatus(type, msg) {
  status.className = type;
  if (type === "loading") {
    status.innerHTML = `<span class="spinner"></span>${msg}`;
  } else {
    status.textContent = msg;
  }
}
