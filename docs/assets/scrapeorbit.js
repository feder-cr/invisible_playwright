/* ScrapeOrbit - company scanner.
   Primary source: Brandfetch Brand Search API (clean, deduplicated brand
   results with logos, built for autocomplete). Needs a free, client-side
   "client id" from https://brandfetch.com/developers - paste it below.
   Fallback: a bundled index of well-known companies, so the scanner still
   works before a client id is set, or if the API is offline. */

(function () {
  "use strict";

  var TELEGRAM = "federcr";

  // Paste your free Brandfetch client id here. It is a PUBLISHABLE key meant to
  // live in front-end code (rate-limited by referrer), not a secret. Empty =
  // the scanner runs on the bundled local index only.
  var BRANDFETCH_CLIENT_ID = "";
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

  function scrapeMessage(company) {
    return "Hi! I'd like to scrape data for: " + company + ". Can you help?";
  }

  function openTelegram(company) {
    var url = "https://t.me/" + TELEGRAM + "?text=" + encodeURIComponent(scrapeMessage(company));
    window.open(url, "_blank", "noopener");
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
        openTelegram(it.name);
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
