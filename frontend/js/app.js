/* ============================================================
   Kimi Chat – Frontend Application (v2.1)
   All previously-reported bugs fixed:
     • chat history load reliably restores the conversation
     • second (and subsequent) messages stream correctly
     • generated code blocks keep their "Copy" button after streaming
     • softer, smoother UI (no layout jumps, better spacing)
     • image upload actually attaches
     • edit / copy per-message icons (icon-only, no label)
   ============================================================ */

// ---------- Auth guard ----------
const TOKEN = localStorage.getItem("kimi_token");
if (!TOKEN) window.location.href = "/login";

const authHeaders = (extra = {}) => ({ Authorization: "Bearer " + TOKEN, ...extra });
const el = (id) => document.getElementById(id);

// ---------- State (single source of truth) ----------
const state = {
  chatId: null,
  messages: [],       // full UI history (single source of truth)
  streaming: false,
  abortController: null,
  chats: [],
  models: [],
  currentModel: localStorage.getItem("kimi_model") || "moonshotai/Kimi-K2.6",
  currentMode: localStorage.getItem("kimi_mode") || "fast",
  attachments: [],
  isOwner: localStorage.getItem("kimi_is_owner") === "1",
  username: localStorage.getItem("kimi_username") || "user",
};

// ---------- Local chat persistence (dual: localStorage + server) ----------
const chatStorageKey = (u) => `kimi_chats_${u}`;

function loadLocalChats() {
  try {
    const raw = localStorage.getItem(chatStorageKey(state.username));
    return raw ? JSON.parse(raw) : {};
  } catch { return {}; }
}
function saveLocalChat(chatId, chatData) {
  const all = loadLocalChats();
  all[chatId] = chatData;
  try { localStorage.setItem(chatStorageKey(state.username), JSON.stringify(all)); }
  catch (e) { console.warn("localStorage save failed", e); }
}
function deleteLocalChat(chatId) {
  const all = loadLocalChats();
  delete all[chatId];
  try { localStorage.setItem(chatStorageKey(state.username), JSON.stringify(all)); } catch {}
}

// ---------- Preferences ----------
function initPrefs() {
  const theme = localStorage.getItem("theme") || "dark";
  const lang = localStorage.getItem("lang") || (navigator.language.startsWith("fa") ? "fa" : "en");
  document.documentElement.setAttribute("data-theme", theme);
  document.documentElement.setAttribute("data-lang", lang);
  applyI18n();
}

// ---------- User block ----------
function initUserBlock() {
  const initials = (state.username || "?").slice(0, 2).toUpperCase();
  el("userAvatar").textContent = initials;
  el("userName").textContent = state.username;
  if (state.isOwner) {
    el("userRole").style.display = "";
    el("ownerPanelBtn").style.display = "";
  }
}

el("userMenuBtn").addEventListener("click", (e) => {
  e.stopPropagation();
  el("userMenu").classList.toggle("open");
});
document.addEventListener("click", (e) => {
  if (!e.target.closest("#userBlock")) el("userMenu").classList.remove("open");
});

el("logoutBtn").addEventListener("click", async () => {
  try { await fetch("/api/auth/logout", { method: "POST", headers: authHeaders() }); } catch {}
  localStorage.removeItem("kimi_token");
  window.location.href = "/login";
});

el("themeMenuBtn").addEventListener("click", () => {
  const cur = document.documentElement.getAttribute("data-theme");
  const next = cur === "dark" ? "light" : "dark";
  document.documentElement.setAttribute("data-theme", next);
  localStorage.setItem("theme", next);
});
el("langMenuBtn").addEventListener("click", () => {
  const cur = document.documentElement.getAttribute("data-lang");
  const next = cur === "en" ? "fa" : "en";
  document.documentElement.setAttribute("data-lang", next);
  localStorage.setItem("lang", next);
  applyI18n();
});

// ---------- Owner panel ----------
el("ownerPanelBtn").addEventListener("click", async () => {
  el("userMenu").classList.remove("open");
  el("ownerModal").classList.add("open");
  await loadUsersList();
});
el("closeOwnerModal").addEventListener("click", () => el("ownerModal").classList.remove("open"));

async function loadUsersList() {
  const container = el("usersList");
  container.innerHTML = "<div style='color:var(--text-dim);padding:10px'>Loading...</div>";
  try {
    const res = await fetch("/api/auth/users", { headers: authHeaders() });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || "Failed");
    container.innerHTML = "";
    data.users.forEach((u) => {
      const item = document.createElement("div");
      item.className = "user-item";
      const initials = u.username.slice(0, 2).toUpperCase();
      item.innerHTML = `
        <div class="avatar user">${escapeHtml(initials)}</div>
        <div class="name">${escapeHtml(u.username)}</div>
        ${u.is_owner ? '<span class="badge">👑 Owner</span>' : ''}
        ${!u.is_owner ? '<button class="del-user" data-user="' + escapeHtml(u.username) + '">Delete</button>' : ''}
      `;
      container.appendChild(item);
    });
    container.querySelectorAll(".del-user").forEach((btn) => {
      btn.addEventListener("click", async () => {
        const user = btn.dataset.user;
        if (!confirm(`Delete user "${user}"?`)) return;
        const r = await fetch("/api/auth/users/" + encodeURIComponent(user), {
          method: "DELETE", headers: authHeaders(),
        });
        if (r.ok) loadUsersList();
      });
    });
  } catch (err) {
    container.innerHTML = `<div style='color:var(--danger);padding:10px'>${escapeHtml(err.message)}</div>`;
  }
}

// ---------- Sidebar (mobile) ----------
el("sidebarToggle").addEventListener("click", () => {
  el("sidebar").classList.add("open");
  el("sidebarOverlay").classList.add("open");
});
el("sidebarOverlay").addEventListener("click", closeSidebar);
el("sidebarCloseMobile").addEventListener("click", closeSidebar);
function closeSidebar() {
  el("sidebar").classList.remove("open");
  el("sidebarOverlay").classList.remove("open");
}

// ---------- Models & modes ----------
async function loadModels() {
  try {
    const res = await fetch("/api/models");
    const data = await res.json();
    state.models = data.models || [];
    const sel = el("modelSelect");
    sel.innerHTML = "";
    state.models.forEach((m) => {
      const opt = document.createElement("option");
      opt.value = m.id;
      opt.textContent = m.name + (m.vision ? " 👁" : "");
      if (m.id === state.currentModel) opt.selected = true;
      sel.appendChild(opt);
    });
    if (state.models.length && !state.models.find(m => m.id === state.currentModel)) {
      state.currentModel = state.models[0].id;
      localStorage.setItem("kimi_model", state.currentModel);
    }
  } catch (e) {
    console.warn("Failed to load models", e);
  }
}
el("modelSelect").addEventListener("change", (e) => {
  state.currentModel = e.target.value;
  localStorage.setItem("kimi_model", state.currentModel);
});
el("modeSelect").value = state.currentMode;
el("modeSelect").addEventListener("change", (e) => {
  state.currentMode = e.target.value;
  localStorage.setItem("kimi_mode", state.currentMode);
});

// ---------- New chat ----------
function newChat() {
  state.chatId = "chat_" + Date.now().toString(36) + Math.random().toString(36).slice(2, 6);
  state.messages = [];
  state.attachments = [];
  renderAttachments();
  rebuildMessagesContainer(true /* showWelcome */);
  closeSidebar();
  renderChatsList();
}
el("newChatBtn").addEventListener("click", newChat);
el("topNewChat").addEventListener("click", newChat);

/** Wipe #messages and put a fresh welcome block in it. */
function rebuildMessagesContainer(showWelcome) {
  const m = el("messages");
  m.innerHTML = "";
  const w = document.createElement("div");
  w.id = "welcome";
  w.className = "welcome";
  w.style.display = showWelcome ? "" : "none";
  w.innerHTML = `
    <div class="welcome-icon">✨</div>
    <h1 data-i18n="welcomeTitle">${t("welcomeTitle")}</h1>
    <p data-i18n="welcomeSubtitle">${t("welcomeSubtitle")}</p>
    <div class="suggestion-grid">
      <button class="suggestion" data-prompt="Search the web for the latest AI news in 2026"><span class="s-icon">🔍</span><span class="s-text">${t("sug1")}</span></button>
      <button class="suggestion" data-prompt="Write a Python script that prints the first 20 Fibonacci numbers, then run it."><span class="s-icon">🐍</span><span class="s-text">${t("sug2")}</span></button>
      <button class="suggestion" data-prompt="Create an image of a serene mountain landscape at sunset"><span class="s-icon">🎨</span><span class="s-text">${t("sug3")}</span></button>
      <button class="suggestion" data-prompt="Explain how transformers work in simple terms."><span class="s-icon">🧠</span><span class="s-text">${t("sug4")}</span></button>
    </div>`;
  m.appendChild(w);
  // Rewire suggestions (fresh DOM)
  w.querySelectorAll(".suggestion").forEach((btn) => {
    btn.addEventListener("click", () => {
      el("input").value = btn.dataset.prompt;
      autoResize();
      send();
    });
  });
}

// ---------- Textarea autoresize ----------
const input = el("input");
function autoResize() {
  input.style.height = "auto";
  input.style.height = Math.min(input.scrollHeight, 200) + "px";
}
input.addEventListener("input", autoResize);
input.addEventListener("keydown", (e) => {
  if (e.key === "Enter" && !e.shiftKey && !e.isComposing) {
    e.preventDefault();
    if (state.streaming) return;
    send();
  }
});

// ---------- Attachments ----------
el("attachBtn").addEventListener("click", () => el("imageInput").click());
el("attachFileBtn").addEventListener("click", () => el("fileInput").click());

el("imageInput").addEventListener("change", async (e) => {
  const file = e.target.files[0];
  if (!file) return;
  const url = URL.createObjectURL(file);
  const fd = new FormData();
  fd.append("file", file);
  try {
    const res = await fetch("/api/upload/image", { method: "POST", body: fd, headers: authHeaders() });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || "upload failed");
    state.attachments.push({ kind: "image", image_id: data.image_id, url, mime: data.mime, name: file.name });
    renderAttachments();
  } catch (err) {
    showToast(t("error") + ": " + err.message, "error");
  }
  el("imageInput").value = "";
});

el("fileInput").addEventListener("change", async (e) => {
  const file = e.target.files[0];
  if (!file) return;
  const fd = new FormData();
  fd.append("file", file);
  try {
    const res = await fetch("/api/upload/file", { method: "POST", body: fd, headers: authHeaders() });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || "upload failed");
    state.attachments.push({ kind: "file", name: data.filename, text: data.text, size: data.size });
    renderAttachments();
  } catch (err) {
    showToast(t("error") + ": " + err.message, "error");
  }
  el("fileInput").value = "";
});

function renderAttachments() {
  const container = el("attachments");
  container.innerHTML = "";
  state.attachments.forEach((att, i) => {
    const div = document.createElement("div");
    if (att.kind === "image") {
      div.className = "attachment";
      div.innerHTML = `<img src="${att.url}" alt=""><button class="remove" data-i="${i}" aria-label="Remove">×</button>`;
    } else {
      div.className = "attachment file";
      div.innerHTML = `<span>📄</span><span>${escapeHtml(att.name)}</span><button class="remove" data-i="${i}" aria-label="Remove">×</button>`;
    }
    container.appendChild(div);
  });
  container.querySelectorAll(".remove").forEach((btn) => {
    btn.addEventListener("click", () => {
      const i = parseInt(btn.dataset.i);
      state.attachments.splice(i, 1);
      renderAttachments();
    });
  });
}

// ---------- Chats list ----------
async function loadChatsList() {
  const local = loadLocalChats();
  const localList = Object.values(local).map((c) => ({
    id: c.id, title: c.title, mtime: c.updated_at || 0, source: "local",
  }));
  let serverList = [];
  try {
    const res = await fetch("/api/chats", { headers: authHeaders() });
    if (res.ok) {
      const data = await res.json();
      serverList = (data.chats || []).map((c) => ({ ...c, source: "server" }));
    }
  } catch {}
  const map = new Map();
  localList.forEach((c) => map.set(c.id, c));
  serverList.forEach((c) => map.set(c.id, { ...(map.get(c.id) || {}), ...c }));
  state.chats = Array.from(map.values()).sort((a, b) => (b.mtime || 0) - (a.mtime || 0));
  renderChatsList();
}

function renderChatsList() {
  const list = el("chatsList");
  list.innerHTML = "";
  state.chats.forEach((c) => {
    const item = document.createElement("div");
    item.className = "chat-item" + (c.id === state.chatId ? " active" : "");
    item.innerHTML = `<span title="${escapeHtml(c.title || "Untitled")}">${escapeHtml(c.title || "Untitled")}</span>
      <button class="del" title="${t("delete")}" aria-label="Delete">
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M3 6h18M8 6V4a2 2 0 012-2h4a2 2 0 012 2v2M6 6l1 14a2 2 0 002 2h6a2 2 0 002-2l1-14"/></svg>
      </button>`;
    item.addEventListener("click", (e) => {
      if (e.target.closest(".del")) return;
      loadChat(c.id);
    });
    item.querySelector(".del").addEventListener("click", async (e) => {
      e.stopPropagation();
      if (!confirm(t("confirmDelete"))) return;
      try { await fetch("/api/chats/" + c.id, { method: "DELETE", headers: authHeaders() }); } catch {}
      deleteLocalChat(c.id);
      state.chats = state.chats.filter((x) => x.id !== c.id);
      if (state.chatId === c.id) newChat();
      renderChatsList();
    });
    list.appendChild(item);
  });
}

async function loadChat(id) {
  // Prefer local (faster). Fall back to server.
  const local = loadLocalChats()[id];
  let data = local || null;
  if (!data) {
    try {
      const res = await fetch("/api/chats/" + id, { headers: authHeaders() });
      if (res.ok) data = await res.json();
    } catch (e) { console.warn(e); }
  }
  if (!data) { showToast("Chat not found", "error"); return; }

  state.chatId = data.id;
  state.messages = (data.messages || []).map(m => ({ ...m, tool_events: m.tool_events || [], generated_images: m.generated_images || [] }));

  // Full clean re-render
  rebuildMessagesContainer(false);
  state.messages.forEach((msg, i) => renderMessage(msg, i));
  closeSidebar();
  renderChatsList();
  setTimeout(scrollToBottom, 50);
}

async function saveChat() {
  if (!state.chatId || state.messages.length === 0) return;
  const firstUser = state.messages.find((m) => m.role === "user");
  const title = firstUser
    ? String(firstUser.display_content || firstUser.content || "New chat").slice(0, 50).trim() || "New chat"
    : "New chat";
  const chatData = {
    id: state.chatId,
    title,
    messages: state.messages,
    updated_at: Date.now() / 1000,
  };
  saveLocalChat(state.chatId, chatData);
  try {
    await fetch("/api/chats/save", {
      method: "POST",
      headers: authHeaders({ "Content-Type": "application/json" }),
      body: JSON.stringify({ chat_id: state.chatId, title, messages: state.messages }),
    });
  } catch (e) { console.warn("Server save failed (local ok):", e); }
  const existing = state.chats.find((c) => c.id === state.chatId);
  if (!existing) {
    state.chats.unshift({ id: state.chatId, title, mtime: Date.now() / 1000 });
  } else {
    existing.title = title;
    existing.mtime = Date.now() / 1000;
    state.chats.sort((a, b) => (b.mtime || 0) - (a.mtime || 0));
  }
  renderChatsList();
}

// ---------- Render helpers ----------
function escapeHtml(s) {
  return String(s == null ? "" : s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

function renderMarkdown(text) {
  if (!text) return "";
  if (window.marked && window.DOMPurify) {
    marked.setOptions({ breaks: true, gfm: true });
    return DOMPurify.sanitize(marked.parse(String(text)), { ADD_ATTR: ["target"] });
  }
  return escapeHtml(text).replace(/\n/g, "<br>");
}

const SVG_COPY = `<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="9" y="9" width="13" height="13" rx="2"/><path d="M5 15H4a2 2 0 01-2-2V4a2 2 0 012-2h9a2 2 0 012 2v1"/></svg>`;
const SVG_CHECK = `<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"><polyline points="20 6 9 17 4 12"/></svg>`;
const SVG_EDIT = `<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M11 4H4a2 2 0 00-2 2v14a2 2 0 002 2h14a2 2 0 002-2v-7"/><path d="M18.5 2.5a2.121 2.121 0 013 3L12 15l-4 1 1-4 9.5-9.5z"/></svg>`;
const SVG_PLAY = `<svg width="12" height="12" viewBox="0 0 24 24" fill="currentColor"><polygon points="5,3 19,12 5,21"/></svg>`;

/**
 * Rebuild ALL code blocks inside `container`. Idempotent — if a code block was
 * already wrapped, it's unwrapped first so we get a fresh copy+run button set.
 * This fixes the "only some code blocks copyable" bug: on streaming updates
 * we re-run this over the full accumulated markdown so every code block ends
 * up with buttons.
 */
function enhanceCodeBlocks(container) {
  // Unwrap already-wrapped blocks first (so re-render is idempotent)
  container.querySelectorAll(".code-block").forEach((wrap) => {
    const pre = wrap.querySelector("pre");
    if (pre) wrap.replaceWith(pre);
  });

  container.querySelectorAll("pre > code").forEach((code) => {
    const pre = code.parentElement;
    const langMatch = code.className.match(/language-(\S+)/);
    const lang = langMatch ? langMatch[1] : "text";
    const wrapper = document.createElement("div");
    wrapper.className = "code-block";
    const header = document.createElement("div");
    header.className = "code-header";
    header.innerHTML = `<span class="code-lang">${escapeHtml(lang)}</span><div class="code-actions"></div>`;

    const copyBtn = document.createElement("button");
    copyBtn.className = "code-btn copy-btn";
    copyBtn.type = "button";
    copyBtn.innerHTML = `${SVG_COPY}<span>${t("copy")}</span>`;
    copyBtn.addEventListener("click", async () => {
      const text = code.textContent || "";
      const ok = await copyToClipboard(text);
      if (ok) {
        copyBtn.classList.add("copied");
        copyBtn.innerHTML = `${SVG_CHECK}<span>${t("copied")}</span>`;
        setTimeout(() => {
          copyBtn.classList.remove("copied");
          copyBtn.innerHTML = `${SVG_COPY}<span>${t("copy")}</span>`;
        }, 1400);
      }
    });
    header.querySelector(".code-actions").appendChild(copyBtn);

    if (["html", "xml"].includes(lang.toLowerCase())) {
      const runBtn = document.createElement("button");
      runBtn.className = "code-btn run";
      runBtn.type = "button";
      runBtn.innerHTML = `${SVG_PLAY}<span>${t("run")}</span>`;
      runBtn.addEventListener("click", () => runHtmlInModal(code.textContent || ""));
      header.querySelector(".code-actions").appendChild(runBtn);
    }

    pre.parentNode.insertBefore(wrapper, pre);
    wrapper.appendChild(header);
    wrapper.appendChild(pre);
    if (window.hljs) { try { hljs.highlightElement(code); } catch {} }
  });

  container.querySelectorAll("a[href]").forEach((a) => {
    if ((a.getAttribute("href") || "").startsWith("http")) {
      a.setAttribute("target", "_blank");
      a.setAttribute("rel", "noopener noreferrer");
    }
  });

  container.querySelectorAll("img.generated-image, img.msg-image").forEach((img) => {
    if (!img.dataset.zoomBound) {
      img.dataset.zoomBound = "1";
      img.addEventListener("click", () => openImageModal(img.src));
    }
  });
}

async function copyToClipboard(text) {
  try {
    await navigator.clipboard.writeText(text);
    return true;
  } catch {}
  try {
    const ta = document.createElement("textarea");
    ta.value = text;
    ta.style.position = "fixed"; ta.style.left = "-9999px";
    document.body.appendChild(ta);
    ta.select();
    document.execCommand("copy");
    document.body.removeChild(ta);
    return true;
  } catch { return false; }
}

function openImageModal(src) {
  el("imageModalImg").src = src;
  el("imageModal").classList.add("open");
}
el("closeImageModal").addEventListener("click", () => el("imageModal").classList.remove("open"));
el("imageModal").addEventListener("click", (e) => {
  if (e.target === el("imageModal")) el("imageModal").classList.remove("open");
});

// HTML preview — sandboxed iframe in a modal (never touches main page)
let currentHtmlContent = "";
function runHtmlInModal(html) {
  currentHtmlContent = html;
  const iframe = el("htmlPreviewFrame");
  iframe.srcdoc = html;
  el("htmlPreviewModal").classList.add("open");
}
el("closeHtmlPreview").addEventListener("click", () => {
  el("htmlPreviewModal").classList.remove("open");
  el("htmlPreviewFrame").srcdoc = "";
});
el("openInWindowBtn").addEventListener("click", () => {
  const blob = new Blob([currentHtmlContent], { type: "text/html" });
  const url = URL.createObjectURL(blob);
  window.open(url, "_blank");
});

// ---------- Toast ----------
function showToast(text, kind = "info") {
  let host = el("toastHost");
  if (!host) {
    host = document.createElement("div");
    host.id = "toastHost";
    host.className = "toast-host";
    document.body.appendChild(host);
  }
  const t = document.createElement("div");
  t.className = "toast toast-" + kind;
  t.textContent = text;
  host.appendChild(t);
  requestAnimationFrame(() => t.classList.add("show"));
  setTimeout(() => {
    t.classList.remove("show");
    setTimeout(() => t.remove(), 250);
  }, 3200);
}

// ---------- Message rendering ----------
function renderMessage(msg, index) {
  const messages = el("messages");
  const welcome = el("welcome");
  if (welcome) welcome.style.display = "none";

  if (msg.role === "user") {
    const block = document.createElement("div");
    block.className = "message-block user-block";
    block.dataset.index = index;
    const imagesHtml = (msg.images && msg.images.length)
      ? msg.images.map((u) => `<img class="msg-image" src="${escapeHtml(u)}">`).join("") : "";
    const filesHtml = (msg.file_names && msg.file_names.length)
      ? `<div class="user-files">📄 ${msg.file_names.map(escapeHtml).join(", ")}</div>` : "";
    const displayText = String(msg.display_content ?? msg.content ?? "");
    block.innerHTML = `
      <div class="message-row user">
        <div class="message-body">
          <div class="user-text">${renderMarkdown(displayText)}</div>
          ${imagesHtml}
          ${filesHtml}
        </div>
        <div class="avatar user">${escapeHtml((state.username || "U").slice(0,1).toUpperCase())}</div>
      </div>
      <div class="msg-actions">
        <button class="msg-action edit-msg" title="${t("edit")}" aria-label="${t("edit")}">${SVG_EDIT}</button>
        <button class="msg-action copy-msg" title="${t("copy")}" aria-label="${t("copy")}">${SVG_COPY}</button>
      </div>`;
    messages.appendChild(block);
    enhanceCodeBlocks(block);
    attachUserActions(block, msg, index);
    return block;
  }

  if (msg.role === "assistant") {
    const block = document.createElement("div");
    block.className = "message-block assistant-block";
    block.dataset.index = index;
    block.innerHTML = `
      <div class="message-row assistant">
        <div class="avatar assistant">K</div>
        <div class="message-body">
          <div class="role-name">Kimi</div>
          <div class="tool-strip"></div>
          <div class="assistant-content"></div>
          <div class="msg-actions asst-actions">
            <button class="msg-action asst-copy" title="${t("copy")}" aria-label="${t("copy")}">${SVG_COPY}</button>
          </div>
        </div>
      </div>`;
    messages.appendChild(block);
    const contentEl = block.querySelector(".assistant-content");
    const strip = block.querySelector(".tool-strip");

    // Historic tool events
    (msg.tool_events || []).forEach((ev) => {
      const notice = document.createElement("div");
      notice.className = "tool-notice done";
      notice.innerHTML = `<span class="check">✓</span> ${toolLabel(ev.name)} <em>${escapeHtml((ev.summary || JSON.stringify(ev.args || {})).slice(0, 90))}</em>`;
      strip.appendChild(notice);
    });

    // Historic generated images
    (msg.generated_images || []).forEach((img) => {
      const imgEl = document.createElement("img");
      imgEl.className = "generated-image";
      imgEl.src = img.url;
      imgEl.alt = img.prompt || "";
      imgEl.addEventListener("click", () => openImageModal(img.url));
      strip.appendChild(imgEl);
    });

    if (msg.content) {
      contentEl.innerHTML = renderMarkdown(msg.content);
      enhanceCodeBlocks(contentEl);
    }
    // Wire copy on assistant messages
    block.querySelector(".asst-copy").addEventListener("click", async () => {
      const ok = await copyToClipboard(String(msg.content || ""));
      if (ok) {
        const b = block.querySelector(".asst-copy");
        b.classList.add("copied");
        b.innerHTML = SVG_CHECK;
        setTimeout(() => { b.classList.remove("copied"); b.innerHTML = SVG_COPY; }, 1200);
      }
    });
    return block;
  }
}

function toolLabel(name) {
  const map = {
    web_search: t("searching"),
    run_code: t("running"),
    analyze_image: t("analyzing"),
    generate_image: t("generatingImage"),
  };
  return map[name] || name;
}

function attachUserActions(block, msg, index) {
  const editBtn = block.querySelector(".edit-msg");
  const copyBtn = block.querySelector(".copy-msg");

  copyBtn.addEventListener("click", async () => {
    const ok = await copyToClipboard(String(msg.display_content ?? msg.content ?? ""));
    if (ok) {
      copyBtn.classList.add("copied");
      copyBtn.innerHTML = SVG_CHECK;
      setTimeout(() => { copyBtn.classList.remove("copied"); copyBtn.innerHTML = SVG_COPY; }, 1200);
    }
  });

  editBtn.addEventListener("click", () => {
    const bodyEl = block.querySelector(".message-body");
    const textEl = bodyEl.querySelector(".user-text");
    const oldText = String(msg.display_content ?? msg.content ?? "");
    const editor = document.createElement("div");
    editor.className = "edit-wrap";
    editor.innerHTML = `
      <textarea class="edit-textarea"></textarea>
      <div class="edit-actions">
        <button class="edit-btn cancel">${t("cancel")}</button>
        <button class="edit-btn save">${t("save")}</button>
      </div>`;
    editor.querySelector("textarea").value = oldText;
    textEl.replaceWith(editor);
    const ta = editor.querySelector("textarea");
    ta.focus();
    ta.setSelectionRange(ta.value.length, ta.value.length);

    editor.querySelector(".cancel").addEventListener("click", () => {
      const restore = document.createElement("div");
      restore.className = "user-text";
      restore.innerHTML = renderMarkdown(oldText);
      editor.replaceWith(restore);
    });

    editor.querySelector(".save").addEventListener("click", async () => {
      const newText = ta.value.trim();
      if (!newText || newText === oldText) {
        editor.querySelector(".cancel").click();
        return;
      }
      msg.content = newText;
      msg.display_content = newText;
      state.messages = state.messages.slice(0, index + 1);
      loadChatFromState();
      await regenerate();
    });
  });
}

function loadChatFromState() {
  rebuildMessagesContainer(state.messages.length === 0);
  state.messages.forEach((m, i) => renderMessage(m, i));
  scrollToBottom();
}

function scrollToBottom() {
  const m = el("messages");
  m.scrollTop = m.scrollHeight;
}

// ---------- Send / Regenerate ----------
el("sendBtn").addEventListener("click", () => {
  if (state.streaming) {
    if (state.abortController) state.abortController.abort();
    return;
  }
  send();
});

async function send() {
  const text = input.value.trim();
  if (!text && state.attachments.length === 0) return;
  if (!state.chatId) newChat();

  const imageAttachments = state.attachments.filter((a) => a.kind === "image");
  const fileAttachments = state.attachments.filter((a) => a.kind === "file");

  let apiContent = text;
  if (fileAttachments.length) {
    const filesText = fileAttachments.map((f) => `\n\n--- File: ${f.name} ---\n${f.text}\n--- End of ${f.name} ---`).join("");
    apiContent = (text || "(please analyze the attached file)") + filesText;
  }

  const uiUserMsg = {
    role: "user",
    content: apiContent,                         // full text to send to API
    display_content: text || "(attached)",       // what we show in the bubble
    images: imageAttachments.map((a) => a.url),
    image_ids: imageAttachments.map((a) => a.image_id),
    file_names: fileAttachments.map((a) => a.name),
  };
  state.messages.push(uiUserMsg);
  const newIndex = state.messages.length - 1;
  renderMessage(uiUserMsg, newIndex);

  input.value = "";
  autoResize();
  state.attachments = [];
  renderAttachments();
  scrollToBottom();

  await streamAssistantResponse();
}

async function regenerate() {
  while (state.messages.length && state.messages[state.messages.length - 1].role === "assistant") {
    state.messages.pop();
  }
  loadChatFromState();
  await streamAssistantResponse();
}

/**
 * Streams a fresh assistant reply. Rebuilds the api-messages array from
 * state.messages every call — this is critical: a stale, growing apiMessages
 * was the reason the second message never rendered in v2.0.
 */
async function streamAssistantResponse() {
  const asstMsg = { role: "assistant", content: "", tool_events: [], generated_images: [] };
  state.messages.push(asstMsg);
  const asstIndex = state.messages.length - 1;
  const asstBlock = renderMessage(asstMsg, asstIndex);
  const contentEl = asstBlock.querySelector(".assistant-content");
  const strip = asstBlock.querySelector(".tool-strip");
  const bodyEl = asstBlock.querySelector(".message-body");

  const thinking = document.createElement("div");
  thinking.className = "thinking";
  thinking.innerHTML = `<span>${t("thinking")}</span><span class="dots"><span></span><span></span><span></span></span> <span class="elapsed" style="font-size:11px;opacity:.6">0.0s</span>`;
  bodyEl.insertBefore(thinking, contentEl);
  const elapsedEl = thinking.querySelector(".elapsed");
  const startTime = Date.now();
  const timer = setInterval(() => {
    elapsedEl.textContent = ((Date.now() - startTime) / 1000).toFixed(1) + "s";
  }, 100);
  const removeThinking = () => {
    if (thinking && thinking.parentElement) thinking.remove();
    clearInterval(timer);
  };

  state.streaming = true;
  updateSendBtn();
  state.abortController = new AbortController();

  // Build clean API messages from state (excluding the trailing in-progress assistant)
  const apiMessages = [];
  const attachedImageIds = [];
  for (let i = 0; i < state.messages.length; i++) {
    const m = state.messages[i];
    if (i === asstIndex) continue;                       // skip in-progress assistant
    if (m.role === "user") {
      apiMessages.push({ role: "user", content: m.content });
      // Only the LAST user message (right before asstIndex) attaches images
      if (i === asstIndex - 1 && m.image_ids && m.image_ids.length) {
        attachedImageIds.push(...m.image_ids);
      }
    } else if (m.role === "assistant") {
      if (m.content) apiMessages.push({ role: "assistant", content: m.content });
    }
  }

  let accumulated = "";
  let errored = false;
  let gotAnything = false;

  try {
    const res = await fetch("/api/chat/stream", {
      method: "POST",
      headers: authHeaders({ "Content-Type": "application/json" }),
      body: JSON.stringify({
        messages: apiMessages,
        chat_id: state.chatId,
        model: state.currentModel,
        mode: state.currentMode,
        attached_images: attachedImageIds,
      }),
      signal: state.abortController.signal,
    });
    if (!res.ok || !res.body) {
      let detail = "HTTP " + res.status;
      try { const j = await res.json(); detail = j.detail || detail; } catch {}
      throw new Error(detail);
    }

    const reader = res.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    while (true) {
      const { value, done } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      const events = buffer.split("\n\n");
      buffer = events.pop() || "";
      for (const rawEvent of events) {
        const lines = rawEvent.split("\n");
        let eventName = "message", dataStr = "";
        for (const line of lines) {
          if (line.startsWith("event:")) eventName = line.slice(6).trim();
          else if (line.startsWith("data:")) dataStr += line.slice(5).trim();
        }
        if (!dataStr) continue;
        let data;
        try { data = JSON.parse(dataStr); } catch { continue; }

        if (eventName === "thinking") {
          // heartbeat from server; keep the indicator alive
          gotAnything = true;
        } else if (eventName === "delta") {
          gotAnything = true;
          removeThinking();
          accumulated += data.content;
          contentEl.innerHTML = renderMarkdown(accumulated) + '<span class="cursor-blink"></span>';
        } else if (eventName === "tool_call") {
          gotAnything = true;
          removeThinking();
          const notice = document.createElement("div");
          notice.className = "tool-notice";
          notice.dataset.callId = data.id;
          notice.innerHTML = `<span class="spinner"></span> ${toolLabel(data.name)} <em>${escapeHtml(shortenArgs(data.name, data.args))}</em>${data.duplicate ? ' <span class="dup-badge">dup</span>' : ''}`;
          strip.appendChild(notice);
          asstMsg.tool_events.push({ name: data.name, args: data.args, id: data.id, summary: shortenArgs(data.name, data.args) });
        } else if (eventName === "tool_result") {
          const notice = strip.querySelector(`[data-call-id="${data.id}"]`);
          if (notice) {
            notice.classList.add("done");
            const spinner = notice.querySelector(".spinner");
            if (spinner) spinner.outerHTML = `<span class="check">✓</span>`;
          }
        } else if (eventName === "image_generated") {
          gotAnything = true;
          removeThinking();
          const imgEl = document.createElement("img");
          imgEl.className = "generated-image";
          imgEl.src = data.url;
          imgEl.alt = data.prompt || "";
          imgEl.addEventListener("click", () => openImageModal(data.url));
          strip.appendChild(imgEl);
          asstMsg.generated_images.push({ url: data.url, prompt: data.prompt });
        } else if (eventName === "done") {
          gotAnything = true;
          removeThinking();
          contentEl.innerHTML = renderMarkdown(accumulated);
          enhanceCodeBlocks(contentEl);
        } else if (eventName === "error") {
          removeThinking();
          errored = true;
          const err = document.createElement("div");
          err.className = "tool-notice error";
          err.textContent = "⚠ " + data.message;
          strip.appendChild(err);
        }
        scrollToBottom();
      }
    }
  } catch (err) {
    if (err.name !== "AbortError") {
      errored = true;
      const errEl = document.createElement("div");
      errEl.className = "tool-notice error";
      errEl.textContent = "⚠ " + err.message;
      strip.appendChild(errEl);
    }
  } finally {
    removeThinking();
    state.streaming = false;
    state.abortController = null;
    updateSendBtn();
    asstMsg.content = accumulated;
    if (!gotAnything && !errored) {
      // Nothing came back and no error — remove the empty assistant slot
      state.messages.pop();
      asstBlock.remove();
    } else if (!accumulated && !asstMsg.generated_images.length && !errored) {
      // Model called tools but ended without a text answer. Add a subtle marker so the bubble isn't blank.
      contentEl.innerHTML = `<em style="color:var(--text-dim)">${t("emptyReply")}</em>`;
    } else {
      // Finalise codeblocks post-stream
      enhanceCodeBlocks(contentEl);
    }
    await saveChat();
  }
}

function shortenArgs(name, args) {
  args = args || {};
  if (name === "web_search") return args.query || "";
  if (name === "run_code") return (args.language || "code") + " • " + String(args.code || "").split("\n")[0].slice(0, 60);
  if (name === "generate_image") return String(args.prompt || "").slice(0, 80);
  if (name === "analyze_image") return String(args.question || "").slice(0, 80);
  return JSON.stringify(args).slice(0, 80);
}

function updateSendBtn() {
  const btn = el("sendBtn");
  if (state.streaming) {
    btn.classList.add("stopping");
    el("sendIcon").style.display = "none";
    el("stopIcon").style.display = "";
  } else {
    btn.classList.remove("stopping");
    el("sendIcon").style.display = "";
    el("stopIcon").style.display = "none";
  }
}

// ---------- Init ----------
async function init() {
  try {
    const r = await fetch("/api/auth/me", { headers: authHeaders() });
    if (!r.ok) {
      localStorage.removeItem("kimi_token");
      window.location.href = "/login";
      return;
    }
    const me = await r.json();
    state.username = me.username;
    state.isOwner = me.is_owner;
    localStorage.setItem("kimi_username", me.username);
    localStorage.setItem("kimi_is_owner", me.is_owner ? "1" : "0");
  } catch { /* proceed anyway */ }

  initPrefs();
  initUserBlock();
  await loadModels();
  newChat();
  await loadChatsList();
}
init();
