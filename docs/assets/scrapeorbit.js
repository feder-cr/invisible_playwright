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

  /* ---- Scrape chat: a ChatGPT-style lead capture. The company is already set
     from the clicked result; we ask for an email, then what to scrape, then
     hand the request to Web3Forms. It collects a request and promises a
     follow-up - it does not pretend to be a live AI returning data. ---- */

  var chat = null; // { company, domain, email, step } while a chat is open

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

  function openScrapeChat(item) {
    var domain = cleanDomain(item.desc) || cleanDomain(item.name);
    chat = { company: item.name, domain: domain, email: "", step: "email" };

    var overlay = makeEl("div", "chat-overlay");
    overlay.id = "chat-overlay";
    overlay.innerHTML =
      '<div class="chat-panel" role="dialog" aria-modal="true" aria-label="Scrape request">' +
        '<div class="chat-head">' +
          '<span class="chat-glyph" id="chat-glyph">' + escapeHtml(initial(item.name)) + '</span>' +
          '<div class="chat-head-body">' +
            '<div class="chat-co">' + escapeHtml(item.name) + '</div>' +
            '<div class="chat-dom">' + escapeHtml(domain) + '</div>' +
          '</div>' +
          '<button class="chat-close" id="chat-close" aria-label="Close">&#215;</button>' +
        '</div>' +
        '<div class="chat-body" id="chat-body"></div>' +
        '<form class="chat-input" id="chat-form">' +
          '<input id="chat-field" type="text" autocomplete="off" placeholder="Your email" aria-label="Message">' +
          '<button class="chat-send" type="submit" aria-label="Send">' + ARROW + '</button>' +
        '</form>' +
      '</div>';
    document.body.appendChild(overlay);
    document.body.style.overflow = "hidden";

    loadInto(overlay.querySelector("#chat-glyph"), [item.logo, item.logoFallback].filter(Boolean));
    overlay.querySelector("#chat-close").addEventListener("click", closeChat);
    overlay.addEventListener("mousedown", function (e) { if (e.target === overlay) closeChat(); });
    document.addEventListener("keydown", chatEsc);
    overlay.querySelector("#chat-form").addEventListener("submit", onChatSubmit);

    botSay("Nice pick. Where should we send your <b>" + escapeHtml(domain) + "</b> data?", function () {
      field().focus();
    });
  }

  function chatEsc(e) { if (e.key === "Escape") closeChat(); }
  function closeChat() {
    var o = document.getElementById("chat-overlay");
    if (o) o.parentNode.removeChild(o);
    document.body.style.overflow = "";
    document.removeEventListener("keydown", chatEsc);
    chat = null;
  }
  function body() { return document.getElementById("chat-body"); }
  function field() { return document.getElementById("chat-field"); }
  function scrollDown() { var b = body(); if (b) b.scrollTop = b.scrollHeight; }
  function addMsg(cls, html) {
    var b = body(); if (!b) return null;
    var m = makeEl("div", "msg " + cls, html);
    b.appendChild(m); scrollDown(); return m;
  }
  function userSay(text) { addMsg("user", escapeHtml(text)); }
  function botSay(html, done) {
    var t = addMsg("bot typing", '<span class="dots"><i></i><i></i><i></i></span>');
    setTimeout(function () {
      if (!t || !t.parentNode) return;
      t.classList.remove("typing");
      t.innerHTML = html;
      scrollDown();
      if (done) done();
    }, 650);
  }
  function offerChips(list) {
    var b = body(); if (!b) return;
    var wrap = makeEl("div", "chips");
    list.forEach(function (c) {
      var chip = makeEl("button", "chip", escapeHtml(c));
      chip.type = "button";
      chip.addEventListener("click", function () {
        if (wrap.parentNode) wrap.parentNode.removeChild(wrap);
        sendRequest(c);
      });
      wrap.appendChild(chip);
    });
    b.appendChild(wrap); scrollDown();
  }

  function onChatSubmit(e) {
    e.preventDefault();
    if (!chat) return;
    var fld = field();
    var val = fld ? fld.value.trim() : "";
    if (!val) { if (fld) fld.focus(); return; }

    if (chat.step === "email") {
      userSay(val);
      fld.value = "";
      if (!validEmail(val)) {
        botSay("Hmm, that doesn't look like an email. Mind trying again?", function () { fld.focus(); });
        return;
      }
      chat.email = val;
      chat.step = "request";
      botSay("Perfect. What do you want to scrape from <b>" + escapeHtml(chat.domain) + "</b>?", function () {
        fld.placeholder = "e.g. all product prices and titles";
        offerChips(["Prices and titles", "Reviews and ratings", "Product images", "Contact info"]);
        fld.focus();
      });
      return;
    }
    if (chat.step === "request") { sendRequest(val); }
  }

  function sendRequest(reqText) {
    if (!chat || chat.step === "sending" || chat.step === "done") return;
    var fld = field();
    if (fld) fld.value = "";
    userSay(reqText);
    chat.step = "sending";
    if (fld) fld.placeholder = "Sending...";
    var t = addMsg("bot typing", '<span class="dots"><i></i><i></i><i></i></span>');

    var payload = {
      access_key: WEB3FORMS_KEY,
      subject: "ScrapeOrbit request: " + chat.company + " (" + chat.domain + ")",
      from_name: "ScrapeOrbit",
      company: chat.company,
      website: chat.domain,
      email: chat.email,
      replyto: chat.email,
      scrape_request: reqText,
      botcheck: ""
    };
    fetch(WEB3FORMS_URL, {
      method: "POST",
      headers: { "Content-Type": "application/json", "Accept": "application/json" },
      body: JSON.stringify(payload)
    }).then(function (r) { return r.json(); }).then(function (data) {
      if (t && t.parentNode) t.parentNode.removeChild(t);
      if (data && data.success) {
        chat.step = "done";
        botSay("On it. We'll email your <b>" + escapeHtml(chat.domain) + "</b> data to <b>" + escapeHtml(chat.email) + "</b> shortly.", lockInput);
      } else {
        chat.step = "request";
        botSay("Something went wrong sending that. Try again in a moment?", function () { if (fld) { fld.placeholder = "Tell us what to scrape"; fld.focus(); } });
      }
    }).catch(function () {
      if (t && t.parentNode) t.parentNode.removeChild(t);
      chat.step = "request";
      botSay("Couldn't reach the server. Check your connection and try again.", function () { if (fld) { fld.placeholder = "Tell us what to scrape"; fld.focus(); } });
    });
  }

  function lockInput() {
    var fld = field(), send = document.querySelector(".chat-send");
    if (fld) { fld.disabled = true; fld.placeholder = "Request sent"; }
    if (send) send.disabled = true;
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
