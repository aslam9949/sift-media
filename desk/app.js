const $ = (id) => document.getElementById(id);
let DATA = null;
let NAV = "front";
let TAB = "wire";

function esc(s) {
  return String(s || "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
}

function kick(c, withWhen) {
  const bits = [`${c.rel != null ? c.rel : "—"}/10`, (c.source || "").toUpperCase(), c.nav_label];
  if (withWhen) bits.push(`${c.when} · ${c.ago}`);
  else bits.push(c.ago);
  return bits.filter(Boolean).join("  ·  ");
}

function filterCards() {
  let list = DATA.cards || [];
  if (NAV !== "front") list = list.filter((c) => c.nav === NAV);
  if (TAB === "digest") list = list.filter((c) => (c.summary || "").trim() || (c.rel || 0) >= 8);
  return list;
}

function renderNav() {
  $("nav").innerHTML = (DATA.nav || [])
    .map((n) => `<button type="button" data-id="${esc(n.id)}" class="${n.id === NAV ? "on" : ""}">${esc(n.label)}</button>`)
    .join("");
  $("nav").onclick = (e) => {
    const b = e.target.closest("button");
    if (!b) return;
    NAV = b.dataset.id;
    paint();
  };
}

function renderTicker() {
  const m = DATA.markets || [];
  const cards = (DATA.cards || []).slice(0, 12);
  const bits = [];
  m.forEach((x) => bits.push(`<b>MKT</b>${esc(x.title)}`));
  cards.forEach((c) => bits.push(`<b>${esc((c.source || "WIRE").toUpperCase())}</b>${esc(c.title)}`));
  if (!bits.length) {
    $("ticker").innerHTML = "<b>SIFT</b>Live desk is on · waiting for tape";
    return;
  }
  const row = bits.join("<span>  ·  </span>");
  $("ticker").innerHTML = row + row;
}

function renderLead(list) {
  const lead = list[0];
  const rail = list.slice(1, 5);
  if (!lead) {
    $("lead").innerHTML = "<p class='empty'>No cards in this section.</p>";
    $("rail").innerHTML = "";
    return;
  }
  const sum = lead.summary ? `<p class="sum">${esc(lead.summary)}</p>` : "";
  $("lead").innerHTML = `<div class="kicker">${esc(kick(lead, true))}</div>
    <h1><a href="${esc(lead.url)}" target="_blank" rel="noopener">${esc(lead.title)}</a></h1>${sum}`;
  $("rail").innerHTML = rail
    .map(
      (c) => `<div class="item"><div class="kicker">${esc(kick(c, false))}</div>
      <h3><a href="${esc(c.url)}" target="_blank" rel="noopener">${esc(c.title)}</a></h3></div>`
    )
    .join("");
}

function take(list, nav, n, skip) {
  return list.filter((c) => c.nav === nav && !skip.has(c.url)).slice(0, n);
}

function renderCols(list, used) {
  const specs = [
    ["world", "WORLD"],
    ["india_politics", "INDIA POLITICS"],
    ["india_markets", "INDIA MARKETS"],
  ];
  $("cols").innerHTML = specs
    .map(([id, lab]) => {
      const items = take(list, id, 4, used);
      items.forEach((c) => used.add(c.url));
      return `<div class="col"><h2>${lab}</h2>${items
        .map(
          (c) => `<a href="${esc(c.url)}" target="_blank" rel="noopener"><div class="meta">${esc((c.source || "").toUpperCase())}  ·  ${esc(c.ago)}  ·  ${esc(String(c.rel))}/10</div>${esc(c.title)}</a>`
        )
        .join("")}</div>`;
    })
    .join("");
}

function renderWire(list, used) {
  const rest = list.filter((c) => !used.has(c.url)).slice(0, 28);
  $("wire").innerHTML = rest
    .map(
      (c) => `<div class="wire-row"><div class="t">${esc(c.when)}<br>${esc(c.ago)}</div>
      <div><h3><a href="${esc(c.url)}" target="_blank" rel="noopener">${esc(c.title)}</a></h3>
      <div class="src">${esc((c.source || "").toUpperCase())}  ·  ${esc(c.nav_label)}  ·  ${esc(String(c.rel))}/10</div></div></div>`
    )
    .join("");
}

function renderCal() {
  const rows = DATA.calendar || [];
  $("cal").innerHTML = rows
    .map(
      (e) => `<div class="cal-row"><div class="d">${esc(e.date)}  ·  ${esc(e.time)}  ·  ${esc(e.country)}</div><b>${esc(e.name)}</b></div>`
    )
    .join("");
  const n = DATA.next_print;
  $("nextprint").textContent = n ? `${n.name}  ·  ${n.time || n.date}` : "";
}

function renderTape() {
  const m = DATA.markets || [];
  $("tape").innerHTML = m.length
    ? m
        .map((x) => `<div class="tape-row">${esc(x.title)}<div class="src">${esc(x.source)}  ·  ${esc(x.ago)}</div></div>`)
        .join("")
    : `<p class="empty">No market cards posted in this window.</p>`;
}

function paint() {
  if (!DATA) return;
  $("clock").textContent = DATA.clock || "";
  $("foot").textContent = `${DATA.count} TELEGRAM POSTS IN THE BOOK  ·  READ-ONLY DESK`;
  renderNav();
  renderTicker();
  const list = filterCards();
  const used = new Set();
  if (NAV === "front") {
    $("front").hidden = false;
    $("section").hidden = true;
    renderLead(list);
    [list[0], ...list.slice(1, 5)].forEach((c) => c && used.add(c.url));
    renderCols(list, used);
    renderWire(list, used);
    renderCal();
    renderTape();
  } else {
    $("front").hidden = true;
    $("section").hidden = false;
    $("seclist").innerHTML =
      `<h2 class="sec">${esc((DATA.nav.find((n) => n.id === NAV) || {}).label || "")}</h2>` +
      list
        .slice(0, 40)
        .map(
          (c) => `<div class="wire-row"><div class="t">${esc(c.when)}<br>${esc(c.ago)}</div>
        <div><h3><a href="${esc(c.url)}" target="_blank" rel="noopener">${esc(c.title)}</a></h3>
        <div class="src">${esc(String(c.rel))}/10  ·  ${esc((c.source || "").toUpperCase())}</div>
        ${c.summary ? `<p class="empty">${esc(c.summary)}</p>` : ""}</div></div>`
        )
        .join("");
  }
}

document.querySelector(".tabs").onclick = (e) => {
  const b = e.target.closest(".tab");
  if (!b) return;
  TAB = b.dataset.tab;
  document.querySelectorAll(".tab").forEach((x) => x.classList.toggle("on", x === b));
  paint();
};

async function load() {
  const r = await fetch("/api/state");
  DATA = await r.json();
  paint();
}
load();
setInterval(load, 60000);
