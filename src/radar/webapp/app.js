// RADAR Mini App. Plain JS, no build step. All text from providers is inserted with textContent.
(() => {
  "use strict";

  const tg = window.Telegram && window.Telegram.WebApp;
  const initData = tg ? tg.initData : "";
  let S = {};
  let lastSession = null;

  const $ = (sel) => document.querySelector(sel);
  const fmt = (key, params = {}) => {
    const tpl = S[key] || key;
    return tpl.replace(/\{(\w+)\}/g, (m, k) => (k in params ? String(params[k]) : m));
  };

  async function api(path, opts = {}) {
    const res = await fetch(path, {
      ...opts,
      headers: { "Content-Type": "application/json", Authorization: "tma " + initData, ...(opts.headers || {}) },
    });
    if (res.status === 204) return null;
    const body = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(body.detail || res.statusText);
    return body;
  }

  function setText(el, value) {
    el.textContent = value || "";
  }

  async function loadStrings(lang) {
    try {
      S = await (await fetch("/api/v1/i18n/" + encodeURIComponent(lang))).json();
    } catch (e) {
      S = {};
    }
    setText($("#tab-search"), S["menu.search"] || "Search");
    setText($("#tab-radars"), S["menu.radars"] || "Radars");
    setText($("#tab-settings"), S["menu.settings"] || "Settings");
    setText($("#go"), S["search.go"] || "Search");
    setText($("#lbl-language"), S["settings.language"] || "Language");
    setText($("#lbl-currency"), S["settings.currency"] || "Currency");
  }

  function showTab(name) {
    document.querySelectorAll(".tab").forEach((t) => t.classList.toggle("active", t.dataset.tab === name));
    document.querySelectorAll(".view").forEach((v) => v.classList.toggle("hidden", v.id !== "view-" + name));
    if (name === "radars") loadRadars();
  }

  const FRESHNESS = {
    live: "Live",
    recent: "Recent",
    timetable: "Timetable",
    directory: "Directory",
    cached_estimate: "Estimate",
    fixture: "DEMO",
  };

  function card(item) {
    const node = $("#card").content.cloneNode(true);
    const el = node.querySelector(".card");
    setText(el.querySelector(".title"), item.title);
    const badge = el.querySelector(".badge");
    const fr = item.provenance.freshness;
    setText(badge, fr === "fixture" ? S["result.fixture"] || "DEMO" : FRESHNESS[fr] || fr);
    badge.classList.add(fr);
    setText(el.querySelector(".sub"), [item.subtitle, item.address].filter(Boolean).join(" | "));

    let price = "";
    if (item.price) {
      price = item.price.formatted;
      if (item.price.kind === "from") price = fmt("result.price_from", { price });
      if (item.converted) price += " (" + fmt("result.price_converted", { converted: item.converted }) + ")";
      if (item.price.kind === "estimate") price += " [" + (S["result.estimate_short"] || "estimate") + "]";
      else if (item.price.unknown_fees) price += " [" + (S["result.taxes_unknown"] || "") + "]";
    }
    setText(el.querySelector(".price"), price);

    const meta = [];
    if (item.starts_at) {
      const s = new Date(item.starts_at);
      meta.push(s.toLocaleString(undefined, { weekday: "short", hour: "2-digit", minute: "2-digit" }));
    }
    if (item.duration_min != null && item.legs.length) {
      meta.push(fmt("result.duration", { hours: Math.floor(item.duration_min / 60),
        minutes: String(item.duration_min % 60).padStart(2, "0") }));
    }
    if (item.legs.length) meta.push(item.legs.map((l) => l.service || l.mode).join(" + "));
    if (item.distance) meta.push(item.distance);
    if (item.rating) meta.push(item.rating.value + "/" + item.rating.scale + (item.rating.count ? " (" + item.rating.count + ")" : ""));
    if (item.opening_hours) meta.push(item.opening_hours);
    setText(el.querySelector(".meta"), meta.join(" | "));

    if (item.met) setText(el.querySelector(".met"), fmt("result.met", { items: item.met }));
    if (item.unverified) setText(el.querySelector(".unverified"), fmt("result.unverified", { items: item.unverified }));
    if (item.unmet) setText(el.querySelector(".unmet"), fmt("result.unmet", { items: item.unmet }));
    if (item.reasons.length) setText(el.querySelector(".why"), fmt("result.why", { reasons: item.reasons.join("; ") }));
    const age = Math.max(0, Math.round((Date.now() - new Date(item.provenance.retrieved_at)) / 60000));
    setText(el.querySelector(".source"), (item.provenance.attribution || item.provenance.provider) + " | " + age + " min");

    const actions = el.querySelector(".actions");
    for (const link of item.links) {
      if (!/^https:\/\//.test(link.url)) continue;
      const a = document.createElement("a");
      a.className = "btn" + (link.kind === "source" ? " secondary" : "");
      a.href = link.url;
      a.rel = "noopener noreferrer";
      a.target = "_blank";
      const label = { booking: "result.book", website: "result.website", menu: "result.menu", source: "result.source_link" }[link.kind];
      setText(a, S[label] || link.kind);
      a.addEventListener("click", (ev) => {
        if (tg) { ev.preventDefault(); tg.openLink(link.url); }
      });
      actions.appendChild(a);
    }
    if (item.point) {
      const m = document.createElement("a");
      m.className = "btn secondary";
      m.href = `https://www.openstreetmap.org/?mlat=${item.point.lat}&mlon=${item.point.lon}#map=18/${item.point.lat}/${item.point.lon}`;
      setText(m, S["result.map"] || "Map");
      m.addEventListener("click", (ev) => { if (tg) { ev.preventDefault(); tg.openLink(m.href); } });
      actions.appendChild(m);
    }
    return node;
  }

  function renderOutcome(data) {
    const results = $("#results");
    results.replaceChildren();
    setText($("#summary"), data.summary ? data.summary.replace(/<[^>]+>/g, "") : "");
    if (data.missing && data.missing.length) {
      setText($("#status"), data.question || "");
      return;
    }
    if (data.coverage) {
      setText($("#status"), data.coverage);
      return;
    }
    const groups = ["exact", "unverified", "alternative"].map((tier) => [tier, (data.headers || {})[tier] || tier]);
    let shown = 0;
    for (const [tier, title] of groups) {
      const items = data.items.filter((i) => i.tier === tier);
      if (!items.length) continue;
      const h = document.createElement("div");
      h.className = "group-title";
      setText(h, title);
      results.appendChild(h);
      items.forEach((i) => { results.appendChild(card(i)); shown += 1; });
    }
    setText($("#status"), shown ? "" : S["search.no_results"] || "No results");
    const errs = Object.keys(data.provider_errors || {});
    const foot = [];
    if (errs.length) foot.push(fmt("search.partial", { providers: errs.join(", ") }));
    if (data.items.some((i) => i.unverified)) foot.push(S["result.unverified_note"] || "");
    setText($("#footer"), foot.join("\n"));
    if (tg && data.session_id && shown) {
      tg.MainButton.setText(S["search.watch"] || "Watch this");
      tg.MainButton.show();
    }
  }

  async function doSearch(text) {
    $("#go").disabled = true;
    setText($("#status"), S["search.progress"] ? fmt("search.progress", { sources: "..." }) : "Searching...");
    try {
      const data = await api("/api/v1/search", { method: "POST", body: JSON.stringify({ text }) });
      lastSession = data.session_id;
      renderOutcome(data);
    } catch (e) {
      setText($("#status"), (S["error.generic"] || "Error") + " (" + e.message + ")");
    } finally {
      $("#go").disabled = false;
    }
  }

  async function watchLast() {
    if (!lastSession) return;
    try {
      await api("/api/v1/radars", { method: "POST", body: JSON.stringify({ session_id: lastSession, kind: "new_results" }) });
      if (tg) tg.showAlert(S["settings.saved"] || "Saved");
    } catch (e) {
      if (tg) tg.showAlert(e.message === "monitoring_not_supported" ? (S["radar.not_supported"] || e.message) : e.message);
    }
  }

  async function loadRadars() {
    const box = $("#radars");
    box.replaceChildren();
    let list = [];
    try { list = await api("/api/v1/radars"); } catch (e) { setText(box, e.message); return; }
    if (!list.length) { setText(box, S["radar.list_empty"] || "No radars"); return; }
    for (const r of list) {
      const c = document.createElement("article");
      c.className = "card";
      const h = document.createElement("h3");
      h.className = "title";
      setText(h, r.name);
      const m = document.createElement("div");
      m.className = "sub";
      setText(m, [S["radar.state." + r.state] || r.state, r.threshold].filter(Boolean).join(" | "));
      const acts = document.createElement("div");
      acts.className = "actions";
      const toggle = document.createElement("button");
      const active = r.state === "active" || r.state === "error";
      setText(toggle, active ? S["radar.pause"] || "Pause" : S["radar.resume"] || "Resume");
      toggle.onclick = async () => {
        await api("/api/v1/radars/" + r.id, { method: "PATCH", body: JSON.stringify({ state: active ? "paused" : "active" }) });
        loadRadars();
      };
      const del = document.createElement("button");
      del.className = "btn secondary";
      setText(del, S["radar.delete"] || "Delete");
      del.onclick = async () => { await api("/api/v1/radars/" + r.id, { method: "DELETE" }); loadRadars(); };
      acts.append(toggle, del);
      c.append(h, m, acts);
      box.appendChild(c);
    }
  }

  async function init() {
    if (tg) {
      tg.ready();
      tg.expand();
      tg.MainButton.onClick(watchLast);
    }
    let me = { language: (tg && tg.initDataUnsafe.user && tg.initDataUnsafe.user.language_code) || "en", currency: "EUR", languages: {} };
    try { me = await api("/api/v1/me"); } catch (e) { setText($("#status"), "Open this page from the RADAR bot in Telegram."); }
    document.documentElement.lang = me.language;
    await loadStrings(me.language);
    const sel = $("#set-lang");
    for (const [code, name] of Object.entries(me.languages || {})) {
      const o = document.createElement("option");
      o.value = code;
      setText(o, name);
      if (code === me.language) o.selected = true;
      sel.appendChild(o);
    }
    $("#set-cur").value = me.currency;
    sel.onchange = async () => { await api("/api/v1/me", { method: "PATCH", body: JSON.stringify({ language: sel.value }) }); await loadStrings(sel.value); };
    $("#set-cur").onchange = async (e) => { await api("/api/v1/me", { method: "PATCH", body: JSON.stringify({ currency: e.target.value }) }); };

    for (const ex of ["sushi in Yerevan tomorrow 19:00", "train Frankfurt to Berlin tomorrow morning", "museum in Tbilisi"]) {
      const b = document.createElement("button");
      b.type = "button";
      b.className = "chip";
      setText(b, ex);
      b.onclick = () => { $("#q").value = ex; doSearch(ex); };
      $("#chips").appendChild(b);
    }
    document.querySelectorAll(".tab").forEach((t) => (t.onclick = () => showTab(t.dataset.tab)));
    $("#search-form").addEventListener("submit", (ev) => {
      ev.preventDefault();
      const text = $("#q").value.trim();
      if (text) doSearch(text);
    });
  }

  init();
})();
