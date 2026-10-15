/* ============================================================
   Features — small, additive behaviours for the commerce feature set.
   Every block is feature-detected: if its markup is absent, it does
   nothing. Safe to remove; nothing here is required to render a page.
   ============================================================ */
(function () {
  "use strict";

  var reduce = window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  function ready(fn) {
    if (document.readyState !== "loading") fn();
    else document.addEventListener("DOMContentLoaded", fn, { once: true });
  }

  ready(function () {
    cookieConsent();
    recentSearches();
    quickView();
    copyButtons();
    shareButtons();
    pincodeAutofill();
    accordions();
  });

  /* ---- Cookie consent banner ---- */
  function cookieConsent() {
    var bar = document.getElementById("cookieBar");
    if (!bar) return;
    var KEY = "indian-store-cookies";
    var saved = null;
    try { saved = localStorage.getItem(KEY); } catch (e) {}
    if (!saved) {
      setTimeout(function () { bar.hidden = false; }, 900);
    }
    bar.querySelectorAll("[data-cookie]").forEach(function (btn) {
      btn.addEventListener("click", function () {
        try { localStorage.setItem(KEY, btn.getAttribute("data-cookie")); } catch (e) {}
        bar.hidden = true;
      });
    });
  }

  /* ---- Recent searches (localStorage) + trending dropdown ---- */
  function recentSearches() {
    var form = document.querySelector(".search-form");
    var input = form && form.querySelector('input[name="q"]');
    var extras = document.getElementById("searchExtras");
    var group = document.getElementById("seRecent");
    var chipsHost = document.getElementById("seRecentChips");
    var clearBtn = document.getElementById("seClear");
    if (!form || !input || !extras) return;
    var KEY = "indian-store-recent";

    function load() {
      try { return JSON.parse(localStorage.getItem(KEY) || "[]"); } catch (e) { return []; }
    }
    function store(items) {
      try { localStorage.setItem(KEY, JSON.stringify(items.slice(0, 5))); } catch (e) {}
    }
    function renderRecent() {
      var items = load();
      if (group) group.hidden = items.length === 0;
      if (!chipsHost) return;
      chipsHost.innerHTML = "";
      items.forEach(function (term) {
        var a = document.createElement("a");
        a.className = "se-chip";
        a.href = form.action + "?q=" + encodeURIComponent(term);
        a.textContent = term;
        chipsHost.appendChild(a);
      });
    }
    renderRecent();

    if (clearBtn) clearBtn.addEventListener("click", function () {
      store([]);
      renderRecent();
    });
    input.addEventListener("focus", function () { extras.hidden = false; });
    input.addEventListener("input", function () { extras.hidden = false; });
    document.addEventListener("click", function (e) {
      if (!form.contains(e.target)) extras.hidden = true;
    });
    form.addEventListener("submit", function () {
      var q = (input.value || "").trim();
      if (!q) return;
      var items = load().filter(function (t) { return t.toLowerCase() !== q.toLowerCase(); });
      items.unshift(q);
      store(items);
    });
  }

  /* ---- Quick view modal ---- */
  function quickView() {
    var modal = document.getElementById("quickView");
    var body = document.getElementById("qvBody");
    if (!modal || !body) return;
    var lastFocus = null;
    function close() {
      modal.hidden = true;
      modal.setAttribute("aria-hidden", "true");
      if (lastFocus && lastFocus.focus) lastFocus.focus();
    }
    modal.querySelectorAll("[data-qv-close]").forEach(function (el) {
      el.addEventListener("click", close);
    });
    document.addEventListener("keydown", function (e) {
      if (e.key === "Escape" && !modal.hidden) close();
    });
    document.addEventListener("click", function (e) {
      var trigger = e.target && e.target.closest ? e.target.closest("[data-qv]") : null;
      if (!trigger) return;
      e.preventDefault();
      lastFocus = trigger;
      var url = trigger.getAttribute("data-qv");
      body.innerHTML = '<p style="padding:20px">Loading…</p>';
      modal.hidden = false;
      modal.setAttribute("aria-hidden", "false");
      fetch(url, { headers: { "X-Requested-With": "fetch" } })
        .then(function (r) { return r.ok ? r.json() : Promise.reject(r.status); })
        .then(function (p) {
          var form = document.querySelector(".search-form");
          body.innerHTML =
            '<div class="qv-media"><img src="' + escapeAttr(p.image) + '" alt="' + escapeAttr(p.name) + '"></div>' +
            '<div class="qv-info">' +
            '<h3>' + escapeHtml(p.name) + '</h3>' +
            '<div class="qv-price">' + escapeHtml(p.price) + '</div>' +
            '<div class="qv-meta">' + escapeHtml(p.category || "") + (p.rating ? " · ★ " + p.rating : "") + (p.stock > 0 ? " · In stock" : " · Out of stock") + '</div>' +
            '<p>' + escapeHtml(p.blurb || "") + '</p>' +
            '<div class="qv-actions">' +
            '<a class="btn-primary" href="' + escapeAttr(p.url) + '">View details</a>' +
            '<form method="post" action="' + escapeAttr(p.add_url) + '"><input type="hidden" name="csrf_token" value="' + escapeAttr(p.csrf) + '"><input type="hidden" name="quantity" value="1"><button class="btn-ghost" type="submit"' + (p.stock > 0 ? "" : " disabled") + '>' + (p.stock > 0 ? "Add to cart" : "Out of stock") + '</button></form>' +
            '</div></div>';
        })
        .catch(function () { body.innerHTML = '<p style="padding:20px">Could not load this product.</p>'; });
    });
  }

  /* ---- Copy-to-clipboard buttons ---- */
  function copyButtons() {
    var buttons = document.querySelectorAll("[data-copy]");
    if (!buttons.length) return;
    var note = document.createElement("div");
    note.className = "copied-note";
    note.textContent = "Link copied";
    document.body.appendChild(note);
    buttons.forEach(function (btn) {
      btn.addEventListener("click", function (e) {
        e.preventDefault();
        var value = btn.getAttribute("data-copy");
        if (value === "location") value = location.href;
        var done = function () {
          note.classList.add("show");
          setTimeout(function () { note.classList.remove("show"); }, 1600);
        };
        if (navigator.clipboard && navigator.clipboard.writeText) {
          navigator.clipboard.writeText(value).then(done).catch(done);
        } else {
          var ta = document.createElement("textarea");
          ta.value = value;
          document.body.appendChild(ta);
          ta.select();
          try { document.execCommand("copy"); } catch (err) {}
          ta.remove();
          done();
        }
      });
    });
  }

  /* ---- Social share buttons ---- */
  function shareButtons() {
    document.querySelectorAll("[data-share]").forEach(function (btn) {
      btn.addEventListener("click", function () {
        var url = encodeURIComponent(location.href);
        var title = encodeURIComponent(document.title);
        var net = btn.getAttribute("data-share");
        var target = "";
        if (net === "whatsapp") target = "https://wa.me/?text=" + title + "%20" + url;
        else if (net === "x") target = "https://twitter.com/intent/tweet?url=" + url + "&text=" + title;
        else if (net === "facebook") target = "https://www.facebook.com/sharer/sharer.php?u=" + url;
        else if (net === "telegram") target = "https://t.me/share/url?url=" + url + "&text=" + title;
        else if (net === "email") target = "mailto:?subject=" + title + "&body=" + url;
        if (target) window.open(target, "_blank", "noopener,noreferrer,width=640,height=560");
      });
    });
  }

  /* ---- Pincode -> city / state autofill ---- */
  var PIN_CITY = {
    "110001": "New Delhi", "400001": "Mumbai", "560001": "Bengaluru",
    "500001": "Hyderabad", "700001": "Kolkata", "600001": "Chennai",
    "411001": "Pune", "380001": "Ahmedabad", "302001": "Jaipur",
    "226001": "Lucknow", "201301": "Noida", "122001": "Gurugram",
    "160017": "Chandigarh", "682011": "Kochi", "641001": "Coimbatore",
    "751001": "Bhubaneswar", "800001": "Patna", "440001": "Nagpur"
  };
  var PIN_STATE = {
    "11": "Delhi", "12": "Haryana", "13": "Punjab", "14": "Punjab", "15": "Punjab",
    "16": "Chandigarh", "17": "Himachal Pradesh", "18": "Jammu & Kashmir", "19": "Jammu & Kashmir",
    "20": "Uttar Pradesh", "21": "Uttar Pradesh", "22": "Uttar Pradesh", "23": "Uttar Pradesh",
    "24": "Uttar Pradesh", "25": "Uttar Pradesh", "26": "Uttar Pradesh", "27": "Uttar Pradesh",
    "28": "Uttar Pradesh", "30": "Rajasthan", "31": "Rajasthan", "32": "Rajasthan", "33": "Rajasthan",
    "34": "Rajasthan", "36": "Gujarat", "37": "Gujarat", "38": "Gujarat", "39": "Gujarat",
    "40": "Maharashtra", "41": "Maharashtra", "42": "Maharashtra", "43": "Maharashtra", "44": "Maharashtra",
    "45": "Madhya Pradesh", "46": "Madhya Pradesh", "47": "Madhya Pradesh", "48": "Madhya Pradesh",
    "49": "Chhattisgarh", "50": "Telangana", "51": "Telangana", "52": "Andhra Pradesh", "53": "Andhra Pradesh",
    "56": "Karnataka", "57": "Karnataka", "58": "Karnataka", "59": "Karnataka", "60": "Tamil Nadu",
    "61": "Tamil Nadu", "62": "Tamil Nadu", "63": "Tamil Nadu", "64": "Tamil Nadu", "67": "Kerala",
    "68": "Kerala", "69": "Kerala", "70": "West Bengal", "71": "West Bengal", "72": "West Bengal",
    "73": "West Bengal", "74": "West Bengal", "75": "Odisha", "76": "Odisha", "77": "Odisha",
    "78": "Assam", "79": "Assam", "80": "Bihar", "81": "Bihar", "82": "Jharkhand", "83": "Jharkhand"
  };
  function setIfEmpty(el, value) {
    if (el && value && !(el.value || "").trim()) el.value = value;
  }
  function pincodeAutofill() {
    document.querySelectorAll("input[data-pincode]").forEach(function (input) {
      var form = input.form || document;
      function apply() {
        var pin = (input.value || "").replace(/\D/g, "");
        if (pin.length < 6) return;
        setIfEmpty(form.querySelector('input[name="city"], input[name="shipping_city"]'), PIN_CITY[pin]);
        var state = form.querySelector('select[name="state"], input[name="state"], input[name="shipping_state"]');
        var stateName = PIN_STATE[pin.slice(0, 2)];
        if (state && stateName && !(state.value || "").trim()) {
          if (state.tagName === "SELECT") {
            var opt = Array.prototype.find.call(state.options, function (o) {
              return o.value === stateName || o.text === stateName;
            });
            if (opt) state.value = opt.value;
          } else {
            state.value = stateName;
          }
        }
      }
      input.addEventListener("blur", apply);
      input.addEventListener("input", function () { if ((input.value || "").length >= 6) apply(); });
    });
  }

  /* ---- Accordions (generic + FAQ) ---- */
  function accordions() {
    document.querySelectorAll("[data-acc]").forEach(function (head) {
      head.addEventListener("click", function () {
        var item = head.closest(".acc, .faq-item");
        if (item) item.classList.toggle("open");
      });
    });
    document.querySelectorAll(".faq-q").forEach(function (q) {
      q.setAttribute("aria-expanded", "false");
      q.addEventListener("click", function () {
        var item = q.closest(".faq-item");
        if (!item) return;
        var open = item.classList.toggle("open");
        q.setAttribute("aria-expanded", open ? "true" : "false");
      });
    });
  }

  /* ---- helpers ---- */
  function escapeHtml(v) {
    return String(v == null ? "" : v).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  }
  function escapeAttr(v) { return escapeHtml(v).replace(/"/g, "&quot;"); }
})();
