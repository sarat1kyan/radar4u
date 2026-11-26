// RADAR Mini App. No build step, no framework. Provider strings are always inserted as text nodes.
(() => {
  "use strict";

  const tg = window.Telegram && window.Telegram.WebApp;
  const initData = tg ? tg.initData : "";
  const $ = (s) => document.querySelector(s);
  let S = {};          // UI strings
  let CAT = null;      // catalog
  let ME = null;
  let draft = null;    // current builder draft
  let last = null;     // last search response
  let map = null;
  let stack = [];      // navigation history

  // ---------- helpers ----------
  function h(tag, attrs, ...kids) {
    const el = document.createElement(tag);
    for (const [k, v] of Object.entries(attrs || {})) {
      if (v == null || v === false) continue;
      if (k === "class") el.className = v;
      else if (k === "style") el.style.cssText = v;
      else if (k.startsWith("on")) el.addEventListener(k.slice(2), v);
      else el.setAttribute(k, v === true ? "" : v);
    }
    for (const kid of kids.flat()) {
      if (kid == null || kid === false) continue;
      el.appendChild(typeof kid === "string" || typeof kid === "number" ? document.createTextNode(String(kid)) : kid);
    }
    return el;
  }
  const t = (k, p = {}) => (S[k] || k).replace(/\{(\w+)\}/g, (m, x) => (x in p ? String(p[x]) : m)).replace(/<[^>]+>/g, "");
  const tp = (k, n) => {
    const tpl = S[k] || "";
    const m = tpl.match(/\{count, plural,(.*)\}$/s);
    if (!m) return String(n);
    const forms = {};
    m[1].replace(/(=\d+|zero|one|two|few|many|other)\s*\{([^}]*)\}/g, (_, f, txt) => (forms[f] = txt));
    let cat = "other";
    try { cat = new Intl.PluralRules(ME ? ME.language : "en").select(n); } catch (e) { /* default */ }
    return (forms["=" + n] || forms[cat] || forms.other || "").replace("#", n);
  };
  const haptic = (kind = "light") => { try { tg && tg.HapticFeedback.impactOccurred(kind); } catch (e) { /* no-op */ } };
  const toast = (msg) => {
    const el = $("#toast");
    el.textContent = msg;
    el.classList.remove("hidden");
    clearTimeout(toast._t);
    toast._t = setTimeout(() => el.classList.add("hidden"), 2600);
  };
  async function api(path, opts = {}) {
    const res = await fetch(path, { ...opts, headers: { "Content-Type": "application/json", Authorization: "tma " + initData } });
    if (res.status === 204) return null;
    const body = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(body.detail || res.statusText);
    return body;
  }
  function openLink(url) { if (tg) tg.openLink(url); else window.open(url, "_blank", "noopener"); }

  const ICONS = {
    dining: '<path d="M7 3v8a2 2 0 0 0 2 2v8M7 3v5M11 3v5M17 3c-2 2-2 6 0 9v9"/>',
    stay: '<path d="M3 18V8M3 14h18v4M21 18v-5a3 3 0 0 0-3-3h-7v4"/><circle cx="7" cy="11" r="2"/>',
    rail: '<rect x="6" y="3" width="12" height="13" rx="3"/><path d="M6 10h12M9 20l-2 2M15 20l2 2M9 16v4M15 16v4"/><circle cx="9" cy="13" r=".5"/><circle cx="15" cy="13" r=".5"/>',
    flight: '<path d="M10 21l2-7-7-3 1-2 8 1 4-6a1.5 1.5 0 0 1 2 2l-4 6 1 8-2 1-3-7"/>',
    event: '<path d="M4 8a2 2 0 0 0 0 4v4a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2v-4a2 2 0 0 0 0-4V6a2 2 0 0 0-2-2H6a2 2 0 0 0-2 2z"/><path d="M13 4v14" stroke-dasharray="2 2"/>',
    wellness: '<path d="M12 21c-5-3-8-6.5-8-11a4 4 0 0 1 8-1 4 4 0 0 1 8 1c0 4.5-3 8-8 11z"/>',
    ground: '<rect x="4" y="4" width="16" height="13" rx="3"/><path d="M4 11h16M7 21v-4M17 21v-4"/><circle cx="8" cy="14" r=".5"/><circle cx="16" cy="14" r=".5"/>',
    realestate: '<path d="M3 11 12 4l9 7"/><path d="M5 10v10h14V10M10 20v-5h4v5"/>',
    plan: '<path d="M9 4 3 6v14l6-2 6 2 6-2V4l-6 2z"/><path d="M9 4v14M15 6v14"/>',
    shopping: '<path d="M5 8h14l-1 12H6z"/><path d="M9 8a3 3 0 0 1 6 0"/>',
  };
  const GRAD = {
    dining: "#ff7a45,#ff4d6d", stay: "#7c5cff,#4f7cff", rail: "#00b386,#00a3ff", flight: "#3a8bff,#6a5cff",
    event: "#ff4d97,#ff8a3d", wellness: "#22c1a1,#5ad17a", ground: "#ffb020,#ff7a45", realestate: "#5b6cff,#8f5cff",
    plan: "#0bb3c4,#3a8bff", shopping: "#ff8a3d,#ffb020",
  };
  const icon = (d) => {
    const s = document.createElementNS("http://www.w3.org/2000/svg", "svg");
    s.setAttribute("viewBox", "0 0 24 24");
    s.innerHTML = ICONS[d] || ICONS.plan; // static, trusted markup
    return s;
  };

  // ---------- navigation ----------
  function show(view, push = true) {
    const cur = document.querySelector(".view:not(.hidden)");
    if (push && cur && cur.id !== "v-" + view) stack.push(cur.id.slice(2));
    document.querySelectorAll(".view").forEach((v) => v.classList.toggle("hidden", v.id !== "v-" + view));
    document.querySelectorAll(".tabbar button").forEach((b) => b.classList.toggle("on", b.dataset.tab === view));
    window.scrollTo({ top: 0 });
    if (tg) {
      if (stack.length && view !== "home") tg.BackButton.show(); else tg.BackButton.hide();
      if (view !== "builder" && view !== "results") tg.MainButton.hide();
    }
  }
  function back() {
    if (!$("#sheet").classList.contains("open")) {
      const prev = stack.pop() || "home";
      show(prev, false);
      if (prev === "builder") renderBuilder();
      if (prev === "results" && last) mainWatch();
    } else closeSheet();
  }

  // ---------- home ----------
  function renderHome() {
    const home = ME.home;
    const pill = $("#city-pill");
    pill.replaceChildren(h("span", null, home ? home.name : t("sb.where")));
    $("#t-tagline").textContent = t("home.tagline");
    $("#t-categories").textContent = t("search.pick_domain");
    $("#q").placeholder = t("home.hint");
    const tiles = $("#tiles");
    tiles.replaceChildren();
    for (const d of CAT.domains) {
      const [a, b] = (GRAD[d.id] || GRAD.plan).split(",");
      tiles.appendChild(h("button", { class: "tile", onclick: () => { haptic(); startBuilder(d.id); } },
        h("span", { class: "ic", style: `background:linear-gradient(135deg,${a},${b})` }, icon(d.id)),
        h("b", null, d.label)));
    }
  }

  // ---------- builder ----------
  const domain = () => CAT.domains.find((d) => d.id === draft.domain);
  function startBuilder(dom) {
    const d = CAT.domains.find((x) => x.id === dom);
    draft = { domain: dom, flags: [], sort: "best", pax: 2 };
    if (!d.categories.length) draft.cat = "any";
    if (d.steps.includes("city") && ME.home) draft.city = ME.home;
    if (d.steps.includes("origin") && ME.home) draft.origin = ME.home;
    const today = new Date();
    draft.day = iso(today);
    if (dom === "stay") { draft.day = iso(addDays(today, 1)); draft.nights = 2; }
    show("builder");
    renderBuilder();
  }
  const iso = (d) => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
  const addDays = (d, n) => { const x = new Date(d); x.setDate(x.getDate() + n); return x; };

  function block(label, ...content) {
    return h("div", { class: "card pad" }, h("p", { class: "step-label" }, label), ...content);
  }
  function chip(label, on, onclick, extra = "") {
    return h("button", { class: `chip ${on ? "on" : ""} ${extra}`, type: "button", onclick: () => { haptic(); onclick(); } }, label);
  }

  function placeBlock(field, label) {
    const saved = CAT.places || [];
    const cur = draft[field];
    const chips = h("div", { class: "chips" });
    const seen = new Set();
    const all = cur ? [cur, ...saved] : saved;
    for (const p of all) {
      if (seen.has(p.canonical_id)) continue;
      seen.add(p.canonical_id);
      chips.appendChild(chip(p.name, cur && cur.canonical_id === p.canonical_id, () => { draft[field] = p; renderBuilder(); }));
    }
    if (tg && tg.LocationManager && field !== "dest") {
      chips.appendChild(chip(t("sb.near_me"), false, () => nearMe(field), "ok"));
    }
    const input = h("input", { placeholder: t("sb.other_city"), enterkeyhint: "search" });
    const find = async () => {
      const q = input.value.trim();
      if (q.length < 2) return;
      try {
        const found = await api("/api/v1/places?q=" + encodeURIComponent(q));
        if (!found.length) { toast(t("error.unknown_place", { place: q })); return; }
        draft[field] = found[0];
        CAT.places = [found[0], ...(CAT.places || [])].slice(0, 8);
        renderBuilder();
      } catch (e) { toast(e.message); }
    };
    input.addEventListener("keydown", (e) => { if (e.key === "Enter") { e.preventDefault(); find(); } });
    return block(label, chips, h("div", { class: "field" }, input, h("button", { type: "button", onclick: find }, t("search.go"))));
  }

  function nearMe(field) {
    const lm = tg.LocationManager;
    const go = () => lm.getLocation((loc) => {
      if (!loc) { toast(t("location.ask")); return; }
      api(`/api/v1/places?q=${loc.latitude.toFixed(5)},${loc.longitude.toFixed(5)}`).then((r) => {
        if (r.length) { draft[field] = r[0]; renderBuilder(); }
      });
    });
    if (lm.isInited) go(); else lm.init(go);
  }

  function dayBlock() {
    const strip = h("div", { class: "chips scroll" });
    const today = new Date();
    const fmt = new Intl.DateTimeFormat(ME.language, { weekday: "short" });
    for (let i = 0; i < 21; i++) {
      const d = addDays(today, i);
      const v = iso(d);
      strip.appendChild(h("button", { class: `day ${draft.day === v ? "on" : ""}`, type: "button",
        onclick: () => { haptic(); draft.day = v; renderBuilder(); } },
        h("small", null, i === 0 ? t("search.today") : fmt.format(d)), h("b", null, String(d.getDate()))));
    }
    const other = h("input", { type: "date", min: iso(today), value: draft.day || "" });
    other.addEventListener("change", () => { if (other.value) { draft.day = other.value; renderBuilder(); } });
    return block(t("sb.ask.day"), strip, h("div", { class: "field" }, other));
  }

  function timeBlock() {
    const transport = ["rail", "ground", "flight"].includes(draft.domain);
    const slots = transport ? ["06:00", "08:00", "10:00", "12:00", "14:00", "16:00", "18:00", "20:00"]
      : ["09:00", "12:00", "13:00", "14:00", "18:00", "19:00", "20:00", "21:00", "22:00"];
    const c = h("div", { class: "chips" }, chip(t("sb.any_time"), !draft.time, () => { delete draft.time; renderBuilder(); }));
    for (const s of slots) c.appendChild(chip(s, draft.time === s, () => { draft.time = s; renderBuilder(); }));
    return block(t("sb.ask.time"), c);
  }

  function stepper(label, key, min, max, fmt) {
    const val = draft[key] || min;
    return block(label, h("div", { class: "stepper" },
      h("button", { type: "button", onclick: () => { haptic(); draft[key] = Math.max(min, val - 1); renderBuilder(); } }, "-"),
      h("b", null, fmt ? fmt(val) : String(val)),
      h("button", { type: "button", onclick: () => { haptic(); draft[key] = Math.min(max, val + 1); renderBuilder(); } }, "+")));
  }

  function renderBuilder() {
    const d = domain();
    $("#b-eyebrow").textContent = "RADAR";
    $("#b-title").textContent = d.label;
    const box = $("#b-steps");
    box.replaceChildren();
    for (const step of d.steps) {
      if (step === "cat" && d.categories.length) {
        const c = h("div", { class: "chips" });
        for (const cat of d.categories) c.appendChild(chip(cat.label, draft.cat === cat.id, () => { draft.cat = cat.id; renderBuilder(); }));
        box.appendChild(block(t("sb.ask.cat"), c));
      } else if (step === "city") box.appendChild(placeBlock("city", t("sb.ask.city")));
      else if (step === "origin") box.appendChild(placeBlock("origin", t("sb.ask.origin")));
      else if (step === "dest") box.appendChild(placeBlock("dest", t("sb.ask.dest")));
      else if (step === "day") box.appendChild(dayBlock());
      else if (step === "time") box.appendChild(timeBlock());
      else if (step === "pax") box.appendChild(stepper(t("sb.ask.pax"), "pax", 1, 20, (n) => tp("wizard.party", n)));
      else if (step === "nights") box.appendChild(stepper(t("sb.ask.nights"), "nights", 1, 30, (n) => tp("result.nights", n)));
      else if (step === "rooms") box.appendChild(stepper(t("sb.ask.rooms"), "rooms", 1, 6, (n) => n + "+"));
      else if (step === "filters") {
        if (d.filters.length) {
          const c = h("div", { class: "chips" });
          for (const f of d.filters) {
            const on = draft.flags.includes(f.id);
            c.appendChild(chip(f.label, on, () => {
              draft.flags = on ? draft.flags.filter((x) => x !== f.id) : [...draft.flags, f.id];
              renderBuilder();
            }));
          }
          box.appendChild(block(t("sb.must"), c));
        }
        const s = h("div", { class: "chips" });
        for (const m of d.sorts) s.appendChild(chip(m.label, draft.sort === m.id, () => { draft.sort = m.id; renderBuilder(); }));
        box.appendChild(block(t("search.sort"), s));
      }
    }
    const ready = (!d.steps.includes("city") || draft.city) && (!d.steps.includes("origin") || draft.origin)
      && (!d.steps.includes("dest") || draft.dest);
    if (tg) {
      tg.MainButton.setParams({ text: t("search.go"), is_active: !!ready, is_visible: true, has_shine_effect: !!ready });
      tg.MainButton.offClick(mainWatchHandler);
      tg.MainButton.offClick(runDraft);
      tg.MainButton.onClick(runDraft);
    } else {
      box.appendChild(h("button", { class: "btn block", type: "button", disabled: !ready, onclick: runDraft }, t("search.go")));
    }
  }

  // ---------- search ----------
  async function runDraft() {
    haptic("medium");
    const payload = { ...draft };
    await doSearch({ draft: payload });
  }
  async function doSearch(body) {
    show("results");
    $("#r-list").replaceChildren(...[0, 1, 2, 3].map(() => h("div", { class: "sk" })));
    $("#r-note").textContent = "";
    $("#r-title").textContent = "";
    $("#r-eyebrow").textContent = t("search.progress", { sources: "..." });
    $("#r-chips").replaceChildren();
    $("#r-sort").replaceChildren();
    $("#map").classList.add("hidden");
    if (tg) { tg.MainButton.hide(); tg.MainButton.showProgress && tg.MainButton.showProgress(); }
    try {
      last = await api("/api/v1/search", { method: "POST", body: JSON.stringify(body) });
      renderResults();
      if (tg) tg.HapticFeedback.notificationOccurred("success");
    } catch (e) {
      $("#r-list").replaceChildren(h("div", { class: "empty" }, t("error.generic")));
      if (tg) tg.HapticFeedback.notificationOccurred("error");
    } finally {
      if (tg && tg.MainButton.hideProgress) tg.MainButton.hideProgress();
    }
  }

  function renderResults() {
    const r = last;
    const summary = (r.summary || "").replace(/<[^>]+>/g, "").split("\n");
    const head = summary[0].split(" | ");
    $("#r-eyebrow").textContent = head[0] || "";
    $("#r-title").textContent = head.slice(1, 3).join(" | ");
    $("#r-chips").replaceChildren(...head.slice(3).concat(summary.slice(1)).filter(Boolean).map((x) => h("span", { class: "chip" }, x)));
    const list = $("#r-list");
    list.replaceChildren();
    if (r.missing && r.missing.length) {
      list.appendChild(h("div", { class: "empty" }, r.question || ""));
      return;
    }
    if (r.coverage) { list.appendChild(h("div", { class: "empty" }, r.coverage.replace(/<[^>]+>/g, ""))); return; }
    if (!r.items.length) { list.appendChild(h("div", { class: "empty" }, t("search.no_results"))); return; }

    const transport = ["rail", "ground", "flight"].includes(r.domain);
    const sortRow = $("#r-sort");
    const sorts = (CAT.domains.find((d) => d.id === r.domain) || { sorts: [] }).sorts;
    sortRow.replaceChildren(...sorts.map((m) => h("button", { type: "button", class: m.id === (draft && draft.sort || "best") ? "on" : "",
      onclick: () => { haptic(); if (draft) { draft.sort = m.id; runDraft(); } } }, m.label)));
    if (!draft) sortRow.replaceChildren();

    let tier = null;
    r.items.forEach((it, i) => {
      if (it.tier !== tier) {
        tier = it.tier;
        list.appendChild(h("div", { class: "group" }, (r.headers || {})[tier] || tier));
      }
      list.appendChild(transport ? tripCard(it, i) : placeCard(it, i));
    });
    const notes = [];
    if (Object.keys(r.provider_errors || {}).length) notes.push(t("search.partial", { providers: Object.keys(r.provider_errors).join(", ") }));
    if (r.items.some((x) => x.unverified)) notes.push(t("result.unverified_note"));
    $("#r-note").textContent = notes.join("\n");
    drawMap(r.items.filter((x) => x.point && !transport));
    mainWatch();
  }

  function priceEl(it) {
    if (!it.price) return null;
    const sub = it.price.kind === "estimate" ? t("result.estimate_short") : it.converted ? it.converted : it.price.unknown_fees ? t("result.taxes_unknown") : null;
    return h("div", { class: "price" }, it.price.kind === "from" ? t("result.price_from", { price: it.price.formatted }) : it.price.formatted,
      sub ? h("small", null, sub) : null);
  }
  function tags(it) {
    const out = [];
    if (it.provenance.freshness === "fixture") out.push(h("span", { class: "tag demo" }, "DEMO"));
    if (it.met_keys.includes("open_at")) out.push(h("span", { class: "tag ok" }, t("result.open_now")));
    if (it.unmet_keys.includes("open_at")) out.push(h("span", { class: "tag bad" }, t("result.closed_then")));
    if (it.unverified) out.push(h("span", { class: "tag warn" }, t("result.unconfirmed_short", { items: it.unverified })));
    if (it.tier === "alternative" && it.unmet) out.push(h("span", { class: "tag bad" }, it.unmet));
    if (it.stars) out.push(h("span", { class: "tag" }, tp("result.stars", it.stars)));
    return out;
  }
  function placeCard(it, i) {
    const ph = h("div", { class: "ph", style: it.photo ? `background-image:url("${encodeURI(it.photo)}")` : "" },
      it.photo ? null : icon(it.domain));
    const sub = [it.subtitle, it.distance].filter(Boolean).join(" | ");
    const rating = it.rating ? h("span", { class: "tag" }, `${it.rating.value}/${it.rating.scale}`) : null;
    return h("div", { class: "card", onclick: () => openDetail(it, i) },
      h("div", { class: "res" }, ph, h("div", { class: "bd" },
        h("h3", null, h("span", { class: "n" }, String(i + 1)), it.title),
        h("div", { class: "sub" }, sub),
        h("div", { class: "row1" }, h("div", { class: "tags" }, ...tags(it), rating), priceEl(it)))));
  }
  const hm = (s) => s ? new Date(s).toLocaleTimeString(ME.language, { hour: "2-digit", minute: "2-digit", timeZone: tzOf(s) }) : "?";
  const tzOf = () => undefined;
  function localTime(isoStr) {
    // Times arrive with their own UTC offset; show the wall-clock part as sent.
    const m = isoStr && isoStr.match(/T(\d{2}):(\d{2})/);
    return m ? `${m[1]}:${m[2]}` : hm(isoStr);
  }
  function tripCard(it, i) {
    const changes = it.transfers === 0 ? t("result.direct") : tp("result.transfers", it.transfers || 0);
    return h("div", { class: "card", onclick: () => openDetail(it, i) }, h("div", { class: "trip" },
      h("div", { class: "times" }, h("span", { class: "t" }, localTime(it.starts_at)), h("div", { class: "line" }), h("span", { class: "t" }, localTime(it.ends_at))),
      h("div", { class: "mid" }, [it.duration, changes].filter(Boolean).join(" | ")),
      h("div", { class: "row1" }, h("div", { class: "sv" }, it.title), priceEl(it)),
      h("div", { class: "tags" }, ...tags(it))));
  }

  function drawMap(items) {
    const el = $("#map");
    if (!window.L || !items.length) { el.classList.add("hidden"); return; }
    el.classList.remove("hidden");
    if (!map) {
      map = L.map(el, { zoomControl: false, attributionControl: true });
      map.attributionControl.setPrefix(false);
      L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", { maxZoom: 19, attribution: "(c) OpenStreetMap" }).addTo(map);
    }
    if (map._radarLayer) map.removeLayer(map._radarLayer);
    const g = L.featureGroup();
    items.slice(0, 30).forEach((it) => {
      const n = last.items.indexOf(it) + 1;
      L.marker([it.point.lat, it.point.lon], { icon: L.divIcon({ className: "", html: `<div class="pin">${n}</div>`, iconSize: [26, 26] }) })
        .on("click", () => openDetail(it, n - 1)).addTo(g);
    });
    g.addTo(map);
    map._radarLayer = g;
    setTimeout(() => { map.invalidateSize(); map.fitBounds(g.getBounds(), { padding: [24, 24], maxZoom: 16 }); }, 50);
  }

  // ---------- detail sheet ----------
  function openDetail(it) {
    haptic();
    const body = $("#sheet-body");
    body.replaceChildren();
    if (it.photo) body.appendChild(h("div", { class: "hero-img", style: `background-image:url("${encodeURI(it.photo)}")` }));
    body.appendChild(h("h2", null, it.title));
    body.appendChild(h("p", { class: "sub" }, [it.subtitle, it.address].filter(Boolean).join(" | ")));
    body.appendChild(h("div", { class: "tags", style: "margin:8px 18px" }, ...tags(it)));

    const facts = [];
    if (it.price) facts.push([t("table.price"), it.price.formatted + (it.converted ? ` (${it.converted})` : "")]);
    if (it.price && it.price.payable_at_property) facts.push(["+", t("result.pay_at_property", { amount: it.price.payable_at_property })]);
    if (it.rating) facts.push([t("table.rating"), `${it.rating.value}/${it.rating.scale} (${it.rating.count || "?"}, ${it.rating.source})`]);
    if (it.distance) facts.push([t("table.distance"), it.distance]);
    if (it.opening_hours) facts.push([t("table.hours"), it.opening_hours]);
    if (it.duration) facts.push([t("table.duration"), it.duration]);
    facts.push([t("table.availability"), t("result.availability." + it.availability)]);
    body.appendChild(h("div", { class: "facts" }, ...facts.map(([a, b]) => h("div", null, h("span", null, a), h("b", null, b)))));

    if (it.legs && it.legs.length) {
      const legs = h("div", { class: "legs" });
      for (const l of it.legs) {
        legs.appendChild(h("div", { class: "leg" }, h("b", null, localTime(l.origin.time)), l.origin.name, l.origin.platform ? ` (${t("result.platform", { platform: l.origin.platform })})` : ""));
        legs.appendChild(h("div", { class: "svc" }, [l.service, l.carrier].filter(Boolean).join(" | ")));
        legs.appendChild(h("div", { class: "leg" }, h("b", null, localTime(l.destination.time)), l.destination.name));
      }
      body.appendChild(legs);
      if (it.provenance.freshness === "timetable") body.appendChild(h("p", { class: "note" }, t("result.timetable_only")));
    }

    const checks = h("ul", { class: "checks" });
    (it.met_keys || []).forEach((k, idx) => checks.appendChild(h("li", { class: "ok" }, (it.met || "").split(", ")[idx] || k)));
    (it.unverified || "").split(", ").filter(Boolean).forEach((x) => checks.appendChild(h("li", { class: "unk" }, `${x} (${t("common.unknown")})`)));
    (it.unmet || "").split(", ").filter(Boolean).forEach((x) => checks.appendChild(h("li", { class: "bad" }, x)));
    if (checks.children.length) body.appendChild(h("div", null, h("p", { class: "step-label", style: "margin:14px 18px 0" }, t("card.requirements")), checks));
    if (it.reasons && it.reasons.length) body.appendChild(h("p", { class: "note" }, t("result.why", { reasons: it.reasons.join("; ") })));

    if (it.point && window.L && !it.legs.length) {
      const mm = h("div", { class: "mini-map" });
      body.appendChild(mm);
      setTimeout(() => {
        const m = L.map(mm, { zoomControl: false, dragging: false, scrollWheelZoom: false, attributionControl: false })
          .setView([it.point.lat, it.point.lon], 16);
        L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", { maxZoom: 19 }).addTo(m);
        L.marker([it.point.lat, it.point.lon], { icon: L.divIcon({ className: "", html: '<div class="pin"></div>', iconSize: [26, 26] }) }).addTo(m);
      }, 60);
    }

    const acts = h("div", { class: "actions" });
    const add = (label, fn, cls = "") => acts.appendChild(h("button", { class: "btn " + cls, type: "button", onclick: fn }, label));
    for (const l of it.links) {
      if (!/^https:\/\//.test(l.url)) continue;
      const key = { booking: "result.book", website: "result.website", menu: "result.menu", source: "result.source_link" }[l.kind];
      add(t(key), () => openLink(l.url), l.kind === "source" ? "ghost" : l.kind === "booking" ? "ok" : "");
    }
    if (it.point) add(t("card.route"), () => openLink(`https://www.google.com/maps/dir/?api=1&destination=${it.point.lat},${it.point.lon}`), "ghost");
    if (it.phone) add(t("card.copy_phone"), () => { navigator.clipboard && navigator.clipboard.writeText(it.phone); toast(it.phone); }, "ghost");
    body.appendChild(acts);
    const age = Math.max(0, Math.round((Date.now() - new Date(it.provenance.retrieved_at)) / 60000));
    body.appendChild(h("p", { class: "note" }, `${it.provenance.attribution || it.provenance.provider} | ${age} min` +
      (it.photo && it.photo.includes("wikimedia") ? `\n${t("card.photo_credit")}` : "")));

    $("#backdrop").classList.remove("hidden");
    $("#sheet").classList.add("open");
    $("#sheet").scrollTop = 0;
    if (tg) tg.BackButton.show();
  }
  function closeSheet() {
    $("#sheet").classList.remove("open");
    $("#backdrop").classList.add("hidden");
    if (tg && !stack.length) tg.BackButton.hide();
  }

  // ---------- watch / radars ----------
  async function mainWatchHandler() {
    if (!last || !last.session_id) return;
    try {
      await api("/api/v1/radars", { method: "POST", body: JSON.stringify({ session_id: last.session_id, kind: "new_results" }) });
      toast(t("settings.saved"));
      if (tg) tg.HapticFeedback.notificationOccurred("success");
    } catch (e) {
      toast(e.message === "monitoring_not_supported" ? t("radar.not_supported") : e.message);
    }
  }
  function mainWatch() {
    if (!tg) return;
    tg.MainButton.offClick(runDraft);
    tg.MainButton.offClick(mainWatchHandler);
    if (last && last.session_id && last.items && last.items.length) {
      tg.MainButton.setParams({ text: t("search.watch"), is_visible: true, is_active: true, has_shine_effect: false });
      tg.MainButton.onClick(mainWatchHandler);
    } else tg.MainButton.hide();
  }

  async function loadRadars() {
    const box = $("#radar-list");
    box.replaceChildren(h("div", { class: "sk" }));
    let list = [];
    try { list = await api("/api/v1/radars"); } catch (e) { box.replaceChildren(h("div", { class: "empty" }, e.message)); return; }
    box.replaceChildren();
    if (!list.length) { box.appendChild(h("div", { class: "empty" }, t("radar.list_empty"))); return; }
    for (const r of list) {
      const active = r.state === "active" || r.state === "error";
      box.appendChild(h("div", { class: "card pad" },
        h("div", { class: "tags" }, h("span", { class: `tag ${active ? "ok" : ""}` }, t("radar.state." + r.state)), r.threshold ? h("span", { class: "tag" }, r.threshold) : null),
        h("h3", { style: "margin:8px 0 4px;font-size:16px" }, r.name),
        h("div", { class: "actions", style: "margin:10px 0 0" },
          h("button", { class: "btn ghost", type: "button", onclick: async () => { haptic(); await api(`/api/v1/radars/${r.id}`, { method: "PATCH", body: JSON.stringify({ state: active ? "paused" : "active" }) }); loadRadars(); } },
            active ? t("radar.pause") : t("radar.resume")),
          h("button", { class: "btn", style: "background:var(--bad)", type: "button", onclick: async () => { haptic("medium"); await api(`/api/v1/radars/${r.id}`, { method: "DELETE" }); loadRadars(); } }, t("radar.delete")))));
    }
  }

  // ---------- init ----------
  async function loadStrings(lang) {
    try { S = await (await fetch("/api/v1/i18n/" + encodeURIComponent(lang))).json(); } catch (e) { S = {}; }
    $("#tab-home").textContent = t("menu.search");
    $("#tab-radars").textContent = t("menu.radars");
    $("#tab-settings").textContent = t("menu.settings");
    $("#t-radars").textContent = t("menu.radars");
    $("#t-settings").textContent = t("menu.settings");
    $("#t-language").textContent = t("settings.language");
    $("#t-currency").textContent = t("settings.currency");
  }

  async function init() {
    if (tg) {
      tg.ready();
      tg.expand();
      try { tg.setHeaderColor("secondary_bg_color"); tg.setBackgroundColor("secondary_bg_color"); } catch (e) { /* older clients */ }
      try { if (tg.isVersionAtLeast("7.7")) tg.disableVerticalSwipes(); } catch (e) { /* older clients */ }
      tg.BackButton.onClick(back);
    }
    try {
      ME = await api("/api/v1/me");
    } catch (e) {
      document.body.replaceChildren(h("div", { class: "empty" }, "Open RADAR from the Telegram bot."));
      return;
    }
    document.documentElement.lang = ME.language;
    await loadStrings(ME.language);
    CAT = await api("/api/v1/catalog");
    renderHome();
    show("home", false);

    const sel = $("#set-lang");
    for (const [code, name] of Object.entries(ME.languages || {})) sel.appendChild(h("option", { value: code, selected: code === ME.language }, name));
    $("#set-cur").value = ME.currency;
    sel.onchange = async () => {
      await api("/api/v1/me", { method: "PATCH", body: JSON.stringify({ language: sel.value }) });
      ME.language = sel.value;
      await loadStrings(sel.value);
      CAT = await api("/api/v1/catalog");
      renderHome();
    };
    $("#set-cur").onchange = async (e) => { await api("/api/v1/me", { method: "PATCH", body: JSON.stringify({ currency: e.target.value }) }); toast(t("settings.saved")); };

    $("#quick").addEventListener("submit", (e) => {
      e.preventDefault();
      const q = $("#q").value.trim();
      if (q) { draft = null; doSearch({ text: q }); }
    });
    $("#city-pill").onclick = () => startBuilder("dining");
    $("#backdrop").onclick = closeSheet;
    document.querySelectorAll(".tabbar button").forEach((b) => (b.onclick = () => {
      haptic();
      stack = [];
      show(b.dataset.tab, false);
      if (b.dataset.tab === "radars") loadRadars();
    }));
  }

  init();
})();
