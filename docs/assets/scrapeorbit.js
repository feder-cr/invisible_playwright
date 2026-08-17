/* ScrapeOrbit - company scanner.
   Primary source: Brandfetch Brand Search API (clean, deduplicated brand
   results with logos, built for autocomplete). Needs a free, client-side
   "client id" from https://brandfetch.com/developers - paste it below.
   Fallback: a bundled index of well-known companies, so the scanner still
   works before a client id is set, or if the API is offline. */

(function () {
  "use strict";

  // Web3Forms access key: a PUBLISHABLE front-end key (like the Brandfetch id).
  // The scrape request lands in the account owner's inbox. Not a secret.
  var WEB3FORMS_KEY = "209e0f52-da13-4b13-9296-c62b97034ac6";
  var WEB3FORMS_URL = "https://api.web3forms.com/submit";

  // Paste your free Brandfetch client id here. It is a PUBLISHABLE key meant to
  // live in front-end code (rate-limited by referrer), not a secret. Empty =
  // the scanner runs on the bundled local index only.
  var BRANDFETCH_CLIENT_ID = "1idtjBhyeyJB_2lvvCA";
  var BRANDFETCH_SEARCH = "https://api.brandfetch.io/v2/search/";
  var BRANDFETCH_CDN = "https://cdn.brandfetch.io/";

  var LIMIT = 8;

  var input = document.getElementById("q");
  var results = document.getElementById("results");
  var status = document.getElementById("status");
  var form = document.getElementById("search-form");

  var fallback = [];
  fetch("assets/fallback.json")
    .then(function (r) { return r.ok ? r.json() : []; })
    .then(function (data) { if (Array.isArray(data)) fallback = data; })
    .catch(function () {});

  /* ---- Scrape instructions: a smart, single-panel lead capture. The company
     is already set from the clicked result; the user writes what to pull in
     plain English (with quick suggestions) plus an email, and we hand it to
     Web3Forms. This is an instruction form, not a chat. It collects a request
     and promises a follow-up - it does not return live data. ---- */

  function makeEl(tag, cls, html) {
    var e = document.createElement(tag);
    if (cls) e.className = cls;
    if (html != null) e.innerHTML = html;
    return e;
  }
  function validEmail(s) {
    return /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(String(s).trim());
  }
  var ARROW = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.3" stroke-linecap="round" stroke-linejoin="round"><path d="M5 12h14M13 6l6 6-6 6"/></svg>';
  var SPARK = '<svg viewBox="0 0 24 24" fill="currentColor"><path d="M12 2l1.7 6.3 6.3 1.7-6.3 1.7L12 18l-1.7-6.3L4 10l6.3-1.7z"/></svg>';
  var CHECK = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.6" stroke-linecap="round" stroke-linejoin="round"><path d="M20 6L9 17l-5-5"/></svg>';

  function openScrapeChat(item) {
    var domain = cleanDomain(item.desc) || cleanDomain(item.name);

    var overlay = makeEl("div", "sp-overlay");
    overlay.id = "sp-overlay";
    overlay.innerHTML =
      '<div class="sp-panel" role="dialog" aria-modal="true" aria-label="Scrape instructions">' +
        '<div class="sp-head">' +
          '<span class="sp-glyph" id="sp-glyph">' + escapeHtml(initial(item.name)) + '</span>' +
          '<div class="sp-head-body">' +
            '<div class="sp-co">' + escapeHtml(item.name) + '</div>' +
            '<div class="sp-dom">' + escapeHtml(domain) + '</div>' +
          '</div>' +
          '<button class="sp-close" id="sp-close" aria-label="Close">&#215;</button>' +
        '</div>' +
        '<form class="sp-body" id="sp-form">' +
          '<label class="sp-label" for="sp-instruct"><span class="sp-spark">' + SPARK + '</span>Scrape instructions</label>' +
          '<textarea id="sp-instruct" class="sp-instruct" rows="4" placeholder="Describe what to pull from ' + escapeHtml(domain) + ' in plain English"></textarea>' +
          '<label class="sp-label" for="sp-email">Send the results to</label>' +
          '<input id="sp-email" class="sp-email" type="email" autocomplete="email" placeholder="you@company.com">' +
          '<p class="sp-note">Plain English is fine. We read your instructions and pull the exact fields.</p>' +
          '<button class="sp-submit" id="sp-submit" type="submit">Send instructions ' + ARROW + '</button>' +
        '</form>' +
      '</div>';
    document.body.appendChild(overlay);
    document.body.style.overflow = "hidden";

    loadInto(overlay.querySelector("#sp-glyph"), [item.logo, item.logoFallback].filter(Boolean));

    overlay.querySelector("#sp-close").addEventListener("click", closeScrapeChat);
    overlay.addEventListener("mousedown", function (e) { if (e.target === overlay) closeScrapeChat(); });
    document.addEventListener("keydown", spEsc);
    overlay.querySelector("#sp-form").addEventListener("submit", function (e) {
      e.preventDefault();
      submitScrape(item.name, domain);
    });
    document.getElementById("sp-instruct").focus();
  }

  function spEsc(e) { if (e.key === "Escape") closeScrapeChat(); }
  function closeScrapeChat() {
    var o = document.getElementById("sp-overlay");
    if (o) o.parentNode.removeChild(o);
    document.body.style.overflow = "";
    document.removeEventListener("keydown", spEsc);
  }

  function resetSubmit(btn) { btn.disabled = false; btn.innerHTML = "Send instructions " + ARROW; }
  function clearSpError() { var e = document.querySelector(".sp-error"); if (e) e.parentNode.removeChild(e); }
  function showSpError(msg) {
    var form = document.getElementById("sp-form"); if (!form) return;
    clearSpError();
    form.appendChild(makeEl("p", "sp-error", escapeHtml(msg)));
  }

  function submitScrape(company, domain) {
    var ta = document.getElementById("sp-instruct");
    var em = document.getElementById("sp-email");
    var btn = document.getElementById("sp-submit");
    if (!ta || !em || !btn) return;
    var instr = ta.value.trim(), email = em.value.trim();
    ta.classList.remove("err"); em.classList.remove("err"); clearSpError();
    if (!instr) { ta.classList.add("err"); ta.focus(); return; }
    if (!validEmail(email)) { em.classList.add("err"); em.focus(); return; }

    btn.disabled = true; btn.innerHTML = "Sending...";
    var payload = {
      access_key: WEB3FORMS_KEY,
      subject: "ScrapeOrbit request: " + company + " (" + domain + ")",
      from_name: "ScrapeOrbit",
      company: company,
      website: domain,
      email: email,
      replyto: email,
      scrape_request: instr,
      botcheck: ""
    };
    fetch(WEB3FORMS_URL, {
      method: "POST",
      headers: { "Content-Type": "application/json", "Accept": "application/json" },
      body: JSON.stringify(payload)
    }).then(function (r) { return r.json(); }).then(function (data) {
      if (data && data.success) { showScrapeDone(domain, email); }
      else { resetSubmit(btn); showSpError("Something went wrong sending that. Try again in a moment."); }
    }).catch(function () {
      resetSubmit(btn); showSpError("Couldn't reach the server. Check your connection and try again.");
    });
  }

  function showScrapeDone(domain, email) {
    var form = document.getElementById("sp-form"); if (!form) return;
    var done = makeEl("div", "sp-done",
      '<div class="sp-check">' + CHECK + '</div>' +
      '<div class="sp-done-t">Instructions sent</div>' +
      '<div class="sp-done-s">We\'ll email your <b>' + escapeHtml(domain) + '</b> data to <b>' + escapeHtml(email) + '</b>.</div>' +
      '<button class="sp-submit" id="sp-done-btn" type="button">Done</button>');
    form.parentNode.replaceChild(done, form);
    var db = document.getElementById("sp-done-btn");
    if (db) db.addEventListener("click", closeScrapeChat);
  }

  function setStatus(text, kind) {
    status.textContent = text;
    status.className = "search-hint" + (kind ? " " + kind : "");
  }

  function escapeHtml(s) {
    return String(s).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  }

  function initial(name) {
    var m = String(name).trim().match(/[A-Za-z0-9]/);
    return m ? m[0].toUpperCase() : "*";
  }

  function cleanDomain(d) {
    return String(d || "").replace(/^https?:\/\//, "").replace(/\/.*$/, "").toLowerCase();
  }
  function faviconOf(host) {
    return "https://www.google.com/s2/favicons?domain=" + encodeURIComponent(host) + "&sz=64";
  }
  function brandfetchLogo(domain) {
    return BRANDFETCH_CDN + encodeURIComponent(domain) + "?c=" + encodeURIComponent(BRANDFETCH_CLIENT_ID);
  }

  // Load the first logo URL that actually resolves; keep the letter glyph if
  // none do (no broken-image icons). Each item may carry a fallback chain.
  function loadInto(glyph, urls) {
    if (!urls.length) return;
    var img = new Image();
    img.alt = "";
    img.onload = function () {
      glyph.classList.add("has-logo");
      glyph.textContent = "";
      glyph.appendChild(img);
    };
    img.onerror = function () { loadInto(glyph, urls.slice(1)); };
    img.src = urls[0];
  }

  function applyLogos(items) {
    items.forEach(function (it, i) {
      var urls = [it.logo, it.logoFallback].filter(Boolean);
      if (!urls.length) return;
      var glyph = results.querySelector('.result-glyph[data-i="' + i + '"]');
      if (glyph) loadInto(glyph, urls);
    });
  }

  function render(items, source) {
    results.innerHTML = "";
    if (!items.length) {
      results.innerHTML = '<p class="results-empty">No signal. Try another name, or message us directly.</p>';
      return;
    }
    items.forEach(function (it, i) {
      var row = document.createElement("div");
      row.className = "result";
      row.style.animationDelay = (i * 45) + "ms";
      row.setAttribute("role", "option");
      row.innerHTML =
        '<span class="result-glyph" data-i="' + i + '">' + escapeHtml(initial(it.name)) + "</span>" +
        '<div class="result-body">' +
          '<div class="result-name">' + escapeHtml(it.name) + "</div>" +
          '<div class="result-desc">' + escapeHtml(it.desc || "company") + "</div>" +
        "</div>" +
        '<button class="scrape-btn" type="button">' +
          '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><path d="M13 2 3 14h7l-1 8 10-12h-7z"/></svg>' +
          "Scrape</button>";
      row.querySelector(".scrape-btn").addEventListener("click", function () {
        openScrapeChat(it);
      });
      results.appendChild(row);
    });
    setStatus(items.length + " result" + (items.length > 1 ? "s" : "") + " from " + source, "live");
  }

  // Accent-insensitive so "Nestle" matches "Nestlé": people type without accents.
  function norm(s) {
    return String(s).toLowerCase().normalize("NFD").replace(/[\u0300-\u036f]/g, "");
  }
  function searchFallback(q) {
    var needle = norm(q);
    return fallback
      .filter(function (c) { return norm(c.name).indexOf(needle) !== -1; })
      .slice(0, LIMIT)
      .map(function (c) {
        return { name: c.name, desc: c.domain || c.desc, logo: c.domain ? faviconOf(c.domain) : null };
      });
  }

  // Brandfetch returns one clean entry per brand/domain. Dedup by domain
  // defensively, and take the logo from the CDN with a favicon fallback.
  function searchBrandfetch(q) {
    if (!BRANDFETCH_CLIENT_ID) return Promise.reject(new Error("no client id"));
    var url = BRANDFETCH_SEARCH + encodeURIComponent(q) + "?c=" + encodeURIComponent(BRANDFETCH_CLIENT_ID);
    return fetch(url).then(function (r) {
      if (!r.ok) throw new Error("http " + r.status);
      return r.json();
    }).then(function (arr) {
      var seen = {}, out = [];
      (arr || []).forEach(function (b) {
        var domain = cleanDomain(b.domain);
        if (!b.name || !domain || seen[domain]) return;
        seen[domain] = 1;
        out.push({
          name: b.name,
          desc: domain,
          logo: b.icon || brandfetchLogo(domain),
          logoFallback: faviconOf(domain)
        });
      });
      return out.slice(0, LIMIT);
    });
  }

  var seq = 0;
  function runSearch(q) {
    var mine = ++seq;
    setStatus("Scanning...", "live");
    searchBrandfetch(q).then(function (items) {
      if (mine !== seq) return;
      if (!items.length) {
        var fb = searchFallback(q);
        render(fb, fb.length ? "local index" : "live index");
        applyLogos(fb);
      } else {
        render(items, "live index");
        applyLogos(items);
      }
    }).catch(function () {
      if (mine !== seq) return;
      var fb = searchFallback(q);
      if (fb.length) { render(fb, "local index"); applyLogos(fb); }
      else setStatus("No signal. Try another name, or message us directly.", "err");
    });
  }

  var timer = null;
  function onInput() {
    var q = input.value.trim();
    if (timer) clearTimeout(timer);
    if (q.length < 2) {
      results.innerHTML = "";
      setStatus("Start typing to search the live index.");
      return;
    }
    timer = setTimeout(function () { runSearch(q); }, 240);
  }

  input.addEventListener("input", onInput);
  form.addEventListener("submit", function (e) {
    e.preventDefault();
    var q = input.value.trim();
    if (q.length >= 2) runSearch(q);
    else input.focus();
  });
})();
