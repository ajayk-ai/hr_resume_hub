"use strict";

// All candidate text comes from resumes and AI summaries, so it is never inserted as HTML:
// elements are built with h() and textContent only.

const COLUMNS = [
  { sort: "hr_star", label: "HR", cls: "col-star", title: "Sort by HR star" },
  { sort: "hod_star", label: "HOD", cls: "col-star", title: "Sort by HOD star" },
  { sort: "name", label: "Candidate" },
  { sort: "position", label: "Applied position" },
  { sort: "department", label: "Department" },
  { sort: "experience", label: "Exp (yrs)", cls: "num" },
  { sort: "score", label: "AI match", cls: "num" },
  { sort: "received", label: "Received" },
  { label: "Resume" },
];
const ASC_FIRST = new Set(["name", "position", "department"]);
const ROLE_LABEL = { hr: "HR", hod: "HOD", admin: "Admin" };

const defaultFilters = () => ({
  q: "", positions: [], positionTerms: [], departments: [], departmentTerms: [],
  expMin: "", expMax: "", scoreMin: "", from: "", to: "", hrStar: "", hodStar: "",
});

const state = {
  user: null,
  filters: defaultFilters(),
  sort: "received", dir: "desc", page: 1, pageSize: 25,
  items: [], total: 0, summary: { total: 0, hr_starred: 0, hod_starred: 0, both_starred: 0, overall_total: 0 },
  detailKey: null,
};

const $ = (selector, root = document) => root.querySelector(selector);

function h(tag, props = {}, ...children) {
  const el = document.createElement(tag);
  for (const [key, value] of Object.entries(props)) {
    if (value === null || value === undefined || value === false) continue;
    if (key === "class") el.className = value;
    else if (key === "text") el.textContent = value;
    else if (key.startsWith("on")) el.addEventListener(key.slice(2), value);
    else el.setAttribute(key, value === true ? "" : String(value));
  }
  for (const child of children.flat()) {
    if (child !== null && child !== undefined && child !== false) el.append(child);
  }
  return el;
}

const dateFormat = new Intl.DateTimeFormat("en-IN", { day: "2-digit", month: "short", year: "numeric", timeZone: "Asia/Kolkata" });
const fmtDate = (iso) => (iso ? dateFormat.format(new Date(iso)) : "—");
const fmtValue = (v) => (v === null || v === undefined || v === "" ? "—" : String(v));
const fmtCount = (n) => Number(n || 0).toLocaleString("en-IN");

function safeUrl(value) {
  try {
    const url = new URL(value);
    return url.protocol === "https:" || url.protocol === "http:" ? url.href : null;
  } catch {
    return null;
  }
}

function debounce(fn, ms) {
  let timer;
  return (...args) => {
    clearTimeout(timer);
    timer = setTimeout(() => fn(...args), ms);
  };
}

let toastTimer;
function toast(message) {
  const el = $("#toast");
  el.textContent = message;
  el.classList.add("show");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => el.classList.remove("show"), 4000);
}

// ---------- API ----------

async function api(path, { method = "GET", body } = {}) {
  const response = await fetch(path, {
    method,
    credentials: "same-origin",
    headers: { "X-Requested-With": "hr-dashboard", ...(body ? { "Content-Type": "application/json" } : {}) },
    body: body ? JSON.stringify(body) : undefined,
  });
  const data = await response.json().catch(() => ({}));
  if (response.status === 401 && path !== "/api/login") {
    showLogin();
    throw new Error(data.error || "Please sign in.");
  }
  if (!response.ok) throw new Error(data.error || `Request failed (${response.status})`);
  return data;
}

function filterParams() {
  const f = state.filters;
  const p = new URLSearchParams();
  if (f.q) p.set("q", f.q);
  f.positions.forEach((v) => p.append("position", v));
  f.positionTerms.forEach((v) => p.append("position_term", v));
  f.departments.forEach((v) => p.append("department", v));
  f.departmentTerms.forEach((v) => p.append("department_term", v));
  for (const [name, value] of [["exp_min", f.expMin], ["exp_max", f.expMax], ["score_min", f.scoreMin], ["received_from", f.from], ["received_to", f.to]]) {
    if (value !== "") p.set(name, value);
  }
  if (f.hrStar) p.set("hr_star", f.hrStar);
  if (f.hodStar) p.set("hod_star", f.hodStar);
  return p;
}

function listParams() {
  const p = filterParams();
  p.set("sort", state.sort);
  p.set("dir", state.dir);
  p.set("page", state.page);
  p.set("page_size", state.pageSize);
  return p;
}

// ---------- loading ----------

let loadSeq = 0;
async function load() {
  const seq = ++loadSeq;
  $("#table").setAttribute("aria-busy", "true");
  try {
    const data = await api(`/api/candidates?${listParams()}`);
    if (seq !== loadSeq) return;
    state.items = data.items;
    state.summary = data.summary;
    state.total = data.summary.total;
    const pages = Math.max(1, Math.ceil(state.total / state.pageSize));
    if (state.page > pages) {
      state.page = pages;
      return load();
    }
    render();
  } catch (error) {
    if (seq === loadSeq) toast(error.message);
  } finally {
    if (seq === loadSeq) $("#table").removeAttribute("aria-busy");
  }
}

function filtersChanged() {
  state.page = 1;
  updateActiveCount();
  load();
}

function activeFilterCount() {
  const f = state.filters;
  return [
    f.q, f.positions.length + f.positionTerms.length, f.departments.length + f.departmentTerms.length,
    f.expMin !== "" || f.expMax !== "", f.scoreMin !== "", f.from !== "" || f.to !== "", f.hrStar, f.hodStar,
  ].filter(Boolean).length;
}

function updateActiveCount() {
  const count = activeFilterCount();
  const badge = $("#active-count");
  badge.hidden = count === 0;
  badge.textContent = `${count} active`;
}

// ---------- multi-select with search ----------

function MultiSelect({ mount, id, field, exactKey, termKey, label, placeholder }) {
  const chips = h("span", { class: "ms-chips" });
  const input = h("input", {
    id: `${id}-input`, type: "text", placeholder, autocomplete: "off", role: "combobox",
    "aria-expanded": "false", "aria-controls": `${id}-pop`, "aria-autocomplete": "list",
  });
  const pop = h("div", { class: "ms-pop", id: `${id}-pop`, hidden: true });
  const box = h("div", { class: "ms-box" }, chips, input);
  const root = h("div", { class: "ms" }, box, pop);
  mount.replaceChildren(root);

  const chosen = () => state.filters[exactKey];
  const terms = () => state.filters[termKey];
  let seq = 0;

  function removeFrom(list, value) {
    const index = list.indexOf(value);
    if (index >= 0) list.splice(index, 1);
  }

  function renderChips() {
    chips.replaceChildren(
      ...terms().map((term) => chip(`contains “${term}”`, "term", () => { removeFrom(terms(), term); changed(); })),
      ...chosen().map((value) => chip(value, "", () => { removeFrom(chosen(), value); changed(); })),
    );
  }

  function chip(text, cls, onRemove) {
    return h("span", { class: `chip ${cls}` },
      h("span", { text }),
      h("button", { type: "button", "aria-label": `Remove ${text}`, onclick: onRemove, text: "×" }));
  }

  function changed() {
    renderChips();
    if (!pop.hidden) refresh();
    filtersChanged();
  }

  function addTerm(text) {
    const term = text.trim();
    if (term && !terms().includes(term)) terms().push(term);
    input.value = "";
    close();
    changed();
  }

  function close() {
    pop.hidden = true;
    input.setAttribute("aria-expanded", "false");
  }

  function open() {
    pop.hidden = false;
    input.setAttribute("aria-expanded", "true");
    refresh();
  }

  async function refresh() {
    const mine = ++seq;
    const query = input.value.trim();
    let data;
    try {
      data = await api(`/api/options/${field}?q=${encodeURIComponent(query)}`);
    } catch (error) {
      if (mine === seq) pop.replaceChildren(h("p", { class: "ms-hint", text: error.message }));
      return;
    }
    if (mine !== seq) return;
    const parts = [];
    if (query && data.total_options > 0) {
      parts.push(h("button", {
        type: "button", class: "ms-all", onclick: () => addTerm(query),
        text: `Filter all ${fmtCount(data.total_candidates)} candidates matching “${query}” (${fmtCount(data.total_options)} variants)`,
      }));
    }
    const list = h("ul", { class: "ms-list", role: "listbox", "aria-multiselectable": "true", "aria-label": label });
    for (const option of data.options) {
      list.append(h("li", { role: "option", "aria-selected": String(chosen().includes(option.value)) },
        h("label", {},
          h("input", {
            type: "checkbox", checked: chosen().includes(option.value),
            onchange: (event) => {
              if (event.target.checked) { if (!chosen().includes(option.value)) chosen().push(option.value); }
              else removeFrom(chosen(), option.value);
              renderChips();
              filtersChanged();
            },
          }),
          h("span", { class: "name", text: option.value }),
          h("span", { class: "n", text: fmtCount(option.count) }))));
    }
    parts.push(list);
    if (!data.options.length) parts.push(h("p", { class: "ms-hint", text: "No matches." }));
    else if (data.total_options > data.options.length) {
      parts.push(h("p", { class: "ms-hint", text: `Showing the ${data.options.length} most common of ${fmtCount(data.total_options)}. Type more to narrow, or press Enter to filter by what you typed.` }));
    } else if (query) {
      parts.push(h("p", { class: "ms-hint", text: "Press Enter to filter by what you typed." }));
    }
    pop.replaceChildren(...parts);
  }

  const refreshSoon = debounce(refresh, 200);
  input.addEventListener("focus", open);
  input.addEventListener("input", () => { if (pop.hidden) open(); else refreshSoon(); });
  input.addEventListener("keydown", (event) => {
    if (event.key === "Enter") { event.preventDefault(); if (input.value.trim()) addTerm(input.value); }
    else if (event.key === "Escape") close();
    else if (event.key === "Backspace" && !input.value) {
      if (chosen().length) chosen().pop(); else if (terms().length) terms().pop(); else return;
      changed();
    }
  });
  document.addEventListener("click", (event) => { if (!root.contains(event.target)) close(); });
  root.addEventListener("focusout", (event) => { if (!root.contains(event.relatedTarget)) close(); });

  renderChips();
  return { renderChips };
}

// ---------- filter panel ----------

let positionSelect, departmentSelect;

function segmented(container, key, name) {
  for (const [value, text] of [["", "Any"], ["yes", "Starred"], ["no", "Not starred"]]) {
    const id = `${name}-${value || "any"}`;
    container.append(
      h("input", { type: "radio", name, id, value, onchange: () => { state.filters[key] = value; filtersChanged(); } }),
      h("label", { for: id, text }));
  }
}

function syncControls() {
  const f = state.filters;
  $("#f-q").value = f.q;
  $("#f-exp-min").value = f.expMin;
  $("#f-exp-max").value = f.expMax;
  $("#f-score").value = f.scoreMin;
  $("#f-from").value = f.from;
  $("#f-to").value = f.to;
  for (const [key, name] of [["hrStar", "star-hr"], ["hodStar", "star-hod"]]) {
    const radio = $(`input[name="${name}"][value="${f[key]}"]`);
    if (radio) radio.checked = true;
  }
  positionSelect.renderChips();
  departmentSelect.renderChips();
  updateActiveCount();
}

function buildFilters() {
  positionSelect = MultiSelect({ mount: $("#ms-position"), id: "ms-position", field: "position", exactKey: "positions", termKey: "positionTerms", label: "Applied position", placeholder: "Type e.g. SAP, quality, design" });
  departmentSelect = MultiSelect({ mount: $("#ms-department"), id: "ms-department", field: "department", exactKey: "departments", termKey: "departmentTerms", label: "Department", placeholder: "Type to search departments" });
  segmented($("#f-hr"), "hrStar", "star-hr");
  segmented($("#f-hod"), "hodStar", "star-hod");

  const bind = (selector, key, event = "change") => {
    $(selector).addEventListener(event, (e) => { state.filters[key] = e.target.value.trim(); filtersChanged(); });
  };
  const search = debounce((value) => { state.filters.q = value.trim(); filtersChanged(); }, 300);
  $("#f-q").addEventListener("input", (e) => search(e.target.value));
  bind("#f-exp-min", "expMin"); bind("#f-exp-max", "expMax"); bind("#f-score", "scoreMin");
  bind("#f-from", "from"); bind("#f-to", "to");

  $("#reset").addEventListener("click", () => {
    state.filters = defaultFilters();
    syncControls();
    filtersChanged();
  });
  $("#page-size").addEventListener("change", (e) => { state.pageSize = Number(e.target.value); state.page = 1; load(); });
  $("#prev").addEventListener("click", () => { state.page -= 1; load(); });
  $("#next").addEventListener("click", () => { state.page += 1; load(); });
  syncControls();
}

// ---------- rendering ----------

function render() {
  renderKpis();
  renderTable();
  const pages = Math.max(1, Math.ceil(state.total / state.pageSize));
  const first = state.total ? (state.page - 1) * state.pageSize + 1 : 0;
  const last = Math.min(state.total, state.page * state.pageSize);
  $("#count").textContent = state.total
    ? `Showing ${fmtCount(first)}–${fmtCount(last)} of ${fmtCount(state.total)} candidates`
    : "No candidates";
  $("#page-info").textContent = `Page ${fmtCount(state.page)} of ${fmtCount(pages)}`;
  $("#prev").disabled = state.page <= 1;
  $("#next").disabled = state.page >= pages;
  $("#empty").hidden = state.total > 0;
}

function toggleStarFilter(keys) {
  const allOn = keys.every((key) => state.filters[key] === "yes");
  keys.forEach((key) => { state.filters[key] = allOn ? "" : "yes"; });
  syncControls();
  filtersChanged();
}

function renderKpis() {
  const s = state.summary;
  const f = state.filters;
  const tile = (cls, value, label, note, keys) => {
    const active = keys && keys.every((key) => f[key] === "yes");
    return h(keys ? "button" : "div", {
      class: `kpi ${cls}`, type: keys ? "button" : null, "aria-pressed": keys ? String(active) : null,
      onclick: keys ? () => toggleStarFilter(keys) : null,
    }, h("span", { class: "value", text: fmtCount(value) }), h("span", { class: "label", text: label }), h("span", { class: "note", text: note }));
  };
  $("#kpis").replaceChildren(
    tile("", s.total, "Candidates", `of ${fmtCount(s.overall_total)} in total`, null),
    tile("hr", s.hr_starred, "HR starred", "in current results · click to filter", ["hrStar"]),
    tile("hod", s.hod_starred, "HOD starred", "in current results · click to filter", ["hodStar"]),
    tile("", s.both_starred, "Starred by both", "in current results · click to filter", ["hrStar", "hodStar"]),
  );
}

function setSort(key) {
  if (state.sort === key) state.dir = state.dir === "asc" ? "desc" : "asc";
  else { state.sort = key; state.dir = ASC_FIRST.has(key) ? "asc" : "desc"; }
  state.page = 1;
  load();
}

function renderHead() {
  const row = h("tr", {});
  for (const column of COLUMNS) {
    if (!column.sort) { row.append(h("th", { class: "plain", scope: "col", text: column.label })); continue; }
    const active = state.sort === column.sort;
    row.append(h("th", { scope: "col", class: column.cls || "", "aria-sort": active ? (state.dir === "asc" ? "ascending" : "descending") : "none" },
      h("button", { type: "button", class: "sort", title: column.title || `Sort by ${column.label}`, onclick: () => setSort(column.sort) },
        column.label, h("span", { class: "arrow", "aria-hidden": "true", text: active ? (state.dir === "asc" ? "▲" : "▼") : "" }))));
  }
  $("#table thead").replaceChildren(row);
}

function starButton(item, role) {
  const star = item[`${role}_star`];
  const mine = state.user.role === role;
  const who = star ? `${ROLE_LABEL[role]} star by ${star.by} on ${fmtDate(star.at)}` : `Not starred by ${ROLE_LABEL[role]}`;
  if (!mine) {
    return h("span", { class: `star ${role} readonly`, role: "img", "aria-label": who, title: who, text: star ? "★" : "☆" });
  }
  return h("button", {
    type: "button", class: `star ${role}`, "aria-pressed": String(Boolean(star)),
    "aria-label": `${ROLE_LABEL[role]} star for ${item.candidate_name || "candidate"}`,
    title: star ? `${who}. Click to remove.` : "Click to star",
    onclick: (event) => { event.stopPropagation(); toggleStar(item, role); },
    text: star ? "★" : "☆",
  });
}

function scoreTier(score) {
  if (score === null || score === undefined || score === "") return "";
  return score >= 80 ? "high" : score >= 60 ? "mid" : "low";
}

function renderTable() {
  renderHead();
  const rows = state.items.map((item) => {
    const link = safeUrl(item.resume_link);
    const row = h("tr", { onclick: () => openDetail(item) },
      h("td", { class: "col-star" }, starButton(item, "hr")),
      h("td", { class: "col-star" }, starButton(item, "hod")),
      h("td", {}, h("div", { class: "cand-name", text: fmtValue(item.candidate_name) }),
        h("div", { class: "cand-sub", text: [item.current_designation, item.current_company].filter(Boolean).join(" · ") })),
      h("td", { class: "pos", text: fmtValue(item.applied_position) }),
      h("td", { text: fmtValue(item.department) }),
      h("td", { class: "num", text: fmtValue(item.total_experience_years) }),
      h("td", { class: "num" }, h("span", { class: `score ${scoreTier(item.ai_match_score)}`, text: fmtValue(item.ai_match_score) })),
      h("td", { text: fmtDate(item.received_at) }),
      h("td", {}, link ? h("a", { href: link, target: "_blank", rel: "noopener noreferrer", onclick: (e) => e.stopPropagation(), text: "Open" }) : "—"));
    row.tabIndex = 0;
    row.addEventListener("keydown", (event) => { if (event.key === "Enter" && event.target === row) openDetail(item); });
    return row;
  });
  $("#table tbody").replaceChildren(...rows);
}

// ---------- stars ----------

async function toggleStar(item, role) {
  const previous = item[`${role}_star`];
  const next = !previous;
  item[`${role}_star`] = next ? { by: state.user.display_name, at: new Date().toISOString() } : null;
  rerenderStars();
  try {
    const stars = await api(`/api/candidates/${item.candidate_key}/star`, { method: "PUT", body: { starred: next } });
    item.hr_star = stars.hr;
    item.hod_star = stars.hod;
  } catch (error) {
    item[`${role}_star`] = previous;
    toast(error.message);
  }
  rerenderStars();
  try {
    state.summary = await api(`/api/summary?${filterParams()}`);
    renderKpis();
  } catch { /* the next load refreshes the tiles */ }
}

function rerenderStars() {
  renderTable();
  const current = state.items.find((item) => item.candidate_key === state.detailKey);
  if (current && $("#detail").open) renderDetail(current);
}

// ---------- detail dialog ----------

function openDetail(item) {
  state.detailKey = item.candidate_key;
  renderDetail(item);
  const dialog = $("#detail");
  if (!dialog.open) dialog.showModal();
}

const initials = (name) => {
  const parts = String(name || "").trim().split(/\s+/).filter(Boolean);
  return ((parts[0] || "?")[0] + (parts.length > 1 ? parts[parts.length - 1][0] : "")).toUpperCase();
};

// The sheet sometimes stores phone numbers as formatted numbers ("9,003,832,041").
const cleanPhone = (value) => (/^\d{1,3}(,\d{3})+$/.test(String(value || "")) ? String(value).replace(/,/g, "") : value);

function scoreCard(score) {
  const value = Number(score);
  const known = score !== null && score !== undefined && score !== "" && Number.isFinite(value);
  const fill = h("div", { class: "meter-fill" });
  fill.style.width = `${known ? Math.max(0, Math.min(100, value)) : 0}%`; // set via CSSOM: inline style attributes are blocked by the CSP
  return h("section", { class: `d-card meter ${scoreTier(score)}` },
    h("h3", { text: "AI match score" }),
    h("div", { class: "meter-value" }, h("strong", { text: known ? String(value) : "—" }), known ? h("span", { text: "/ 100" }) : null),
    h("div", { class: "meter-track", role: "img", "aria-label": known ? `AI match score ${value} out of 100` : "No AI match score" }, fill));
}

function renderDetail(item) {
  const link = safeUrl(item.resume_link);
  const card = (title, pairs) => h("section", { class: "d-card" },
    h("h3", { text: title }),
    h("dl", { class: "d-list" }, ...pairs.map(([label, value]) => h("div", { class: "d-item" },
      h("dt", { text: label }), h("dd", { text: fmtValue(value) })))));
  const textCard = (title, value) => (value ? h("section", { class: "d-card d-text" }, h("h3", { text: title }), h("p", { text: value })) : null);
  const skills = (item.key_skills || "").split("\n").map((s) => s.trim()).filter(Boolean);
  const starNote = (role) => {
    const star = item[`${role}_star`];
    return h("small", { text: star ? `${star.by}, ${fmtDate(star.at)}` : "not starred" });
  };
  $("#detail").replaceChildren(
    h("div", { class: "d-head" },
      h("div", { class: "d-id" },
        h("div", { class: "d-avatar", "aria-hidden": "true", text: initials(item.candidate_name) }),
        h("div", {}, h("h2", { id: "detail-title", text: fmtValue(item.candidate_name) }),
          h("p", { class: "muted", text: fmtValue(item.applied_position) }))),
      h("button", { type: "button", class: "btn", onclick: () => $("#detail").close(), text: "Close" })),
    h("div", { class: "d-body" },
      h("div", { class: "d-stars" },
        ...["hr", "hod"].map((role) => h("div", { class: "d-star" }, h("strong", { text: ROLE_LABEL[role] }), starButton(item, role), starNote(role)))),
      h("div", { class: "d-cards" },
        scoreCard(item.ai_match_score),
        card("Contact", [["Email", item.email], ["Phone", cleanPhone(item.phone)], ["Current location", item.current_location], ["Preferred location", item.preferred_location]]),
        card("Experience", [["Total experience (yrs)", item.total_experience_years], ["Relevant experience (yrs)", item.relevant_experience_years], ["Current company", item.current_company], ["Current designation", item.current_designation], ["Notice period", item.notice_period]]),
        card("Compensation and education", [["Current CTC", item.current_ctc], ["Expected CTC", item.expected_ctc], ["Qualification", [item.highest_qualification, item.specialization].filter(Boolean).join(", ")]]),
        card("Application", [["Department", item.department], ["Received", fmtDate(item.received_at)], ["Sheet source", item.data_source]])),
      skills.length ? h("section", { class: "d-card" }, h("h3", { text: "Key skills" }), h("div", { class: "chips" }, ...skills.map((s) => h("span", { class: "chip", text: s })))) : null,
      textCard("AI summary", item.ai_resume_summary),
      textCard("Strengths", item.ai_strengths),
      textCard("Weaknesses", item.ai_weaknesses),
      textCard("Skill gaps", item.ai_skill_gaps),
      textCard("Remarks", item.remarks),
      link ? h("div", {}, h("a", { class: "btn primary resume-link", href: link, target: "_blank", rel: "noopener noreferrer", text: "Open resume" })) : null));
}

// ---------- admin portal ----------

const PASSWORD_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZabcdefghijkmnpqrstuvwxyz23456789"; // no look-alike characters
let adminUsers = [];

function generatePassword(length = 14) {
  const limit = 256 - (256 % PASSWORD_ALPHABET.length); // rejection sampling keeps the choice unbiased
  const out = [];
  while (out.length < length) {
    for (const byte of crypto.getRandomValues(new Uint8Array(length * 2))) {
      if (byte < limit && out.length < length) out.push(PASSWORD_ALPHABET[byte % PASSWORD_ALPHABET.length]);
    }
  }
  return out.join("");
}

async function loadUsers() {
  try {
    adminUsers = await api("/api/admin/users");
    renderUsers();
  } catch (error) {
    toast(error.message);
  }
}

function renderUsers() {
  const me = state.user.username;
  const active = adminUsers.filter((u) => u.active).length;
  $("#user-count").textContent = `${adminUsers.length} users, ${active} active`;
  $("#users-table tbody").replaceChildren(...adminUsers.map((u) => h("tr", {},
    h("td", { text: u.username }),
    h("td", { text: u.display_name }),
    h("td", {}, h("span", { class: `badge role-${u.role}`, text: ROLE_LABEL[u.role] })),
    h("td", { class: u.active ? "" : "status-off", text: u.active ? "Active" : "Disabled" }),
    h("td", { text: fmtDate(u.created_at) }),
    h("td", {}, h("div", { class: "actions" },
      h("button", { type: "button", class: "btn", onclick: () => openUserDialog(u), text: "Edit" }),
      h("button", {
        type: "button", class: "btn", disabled: u.username === me,
        title: u.username === me ? "You cannot disable your own account" : null,
        onclick: () => toggleActive(u), text: u.active ? "Disable" : "Enable",
      }))))));
}

async function toggleActive(user) {
  const verb = user.active ? "Disable" : "Enable";
  if (!confirm(`${verb} ${user.username}?`)) return;
  try {
    await api(`/api/admin/users/${encodeURIComponent(user.username)}`, { method: "PATCH", body: { active: !user.active } });
    toast(`${user.username} ${user.active ? "disabled" : "enabled"}.`);
  } catch (error) {
    toast(error.message);
  }
  loadUsers();
}

function openUserDialog(user) {
  const isSelf = user.username === state.user.username;
  const error = h("p", { class: "error", role: "alert" });
  const name = h("input", { name: "display_name", value: user.display_name, maxlength: 80, required: true });
  const role = h("select", { name: "role", disabled: isSelf, title: isSelf ? "You cannot change your own role" : null },
    ...Object.entries(ROLE_LABEL).map(([value, label]) => h("option", { value, text: label, selected: value === user.role })));
  const password = h("input", {
    name: "password", type: "password", autocomplete: "new-password", minlength: 10, maxlength: 200,
    placeholder: "Leave blank to keep the current password",
  });
  const generate = h("button", {
    type: "button", class: "btn", text: "Generate",
    onclick: () => { password.value = generatePassword(); password.type = "text"; },
  });
  const dialog = $("#user-dialog");
  const form = h("form", { class: "d-body", onsubmit: async (event) => {
    event.preventDefault();
    error.textContent = "";
    const changes = {};
    if (name.value.trim() !== user.display_name) changes.display_name = name.value.trim();
    if (!role.disabled && role.value !== user.role) changes.role = role.value;
    if (password.value) changes.password = password.value;
    if (!Object.keys(changes).length) { dialog.close(); return; }
    try {
      await api(`/api/admin/users/${encodeURIComponent(user.username)}`, { method: "PATCH", body: changes });
      dialog.close();
      toast(`Saved ${user.username}.`);
      loadUsers();
    } catch (failure) {
      error.textContent = failure.message;
    }
  } },
    h("label", { class: "field" }, "Display name", name),
    h("label", { class: "field" }, "Role", role),
    h("label", { class: "field" }, "New password", h("div", { class: "pw-row" }, password, generate)),
    error,
    h("div", { class: "actions" },
      h("button", { type: "submit", class: "btn primary", text: "Save" }),
      h("button", { type: "button", class: "btn", onclick: () => dialog.close(), text: "Cancel" })));
  dialog.replaceChildren(
    h("div", { class: "d-head" }, h("h2", { id: "user-dialog-title", text: `Edit ${user.username}` })),
    form);
  dialog.showModal();
}

$("#user-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const form = event.target;
  const error = $("#user-form-error");
  error.textContent = "";
  try {
    const created = await api("/api/admin/users", {
      method: "POST",
      body: {
        username: form.elements.username.value, display_name: form.elements.display_name.value,
        role: form.elements.role.value, password: form.elements.password.value,
      },
    });
    form.reset();
    form.elements.password.type = "password";
    toast(`Created ${created.username} (${ROLE_LABEL[created.role]}).`);
    loadUsers();
  } catch (failure) {
    error.textContent = failure.message;
  }
});

$("#gen-password").addEventListener("click", () => {
  const field = $("#user-form").elements.password;
  field.value = generatePassword();
  field.type = "text"; // visible so the admin can copy it
});

// ---------- session ----------

let filtersBuilt = false;

function showLogin() {
  $("#app-view").hidden = true;
  $("#login-view").hidden = false;
  $("#detail").open && $("#detail").close();
  $("#user-dialog").open && $("#user-dialog").close();
  $("#login-form").elements.password.value = "";
}

function showApp() {
  const isAdmin = state.user.role === "admin";
  $("#login-view").hidden = true;
  $("#app-view").hidden = false;
  $("#who-name").textContent = state.user.display_name;
  $("#who-role").textContent = ROLE_LABEL[state.user.role];
  $("#app-title").textContent = isAdmin ? "User administration" : "Candidate Dashboard";
  $("#admin-main").hidden = !isAdmin;
  $("#review-main").hidden = isAdmin;
  if (isAdmin) {
    loadUsers();
    return;
  }
  if (!filtersBuilt) { buildFilters(); filtersBuilt = true; }
  load();
}

$("#login-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const form = event.target;
  $("#login-error").textContent = "";
  try {
    state.user = await api("/api/login", { method: "POST", body: { username: form.elements.username.value, password: form.elements.password.value } });
    showApp();
  } catch (error) {
    $("#login-error").textContent = error.message;
  }
});

$("#logout").addEventListener("click", async () => {
  try { await api("/api/logout", { method: "POST", body: {} }); } catch { /* already signed out */ }
  state.user = null;
  showLogin();
});

$("#detail").addEventListener("click", (event) => { if (event.target === event.currentTarget) event.currentTarget.close(); });

(async function boot() {
  try {
    state.user = await api("/api/me");
    showApp();
  } catch {
    showLogin();
  }
})();
