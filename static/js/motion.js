/* ============================================================
   Motion Plus — the animation controller behind motion-plus.css.
   Additive, dependency-free, reduced-motion aware. Techniques are
   adapted from 21st.dev patterns (cursor spotlight, card spotlight,
   scroll text reveal) plus a marquee, header condense, back-to-top
   and button ripple. Safe to remove: nothing here is required for
   the page to render.
   ============================================================ */
(function () {
  "use strict";

  var reduce = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  var html = document.documentElement;
  html.classList.add("has-js");

  function onReady(fn) {
    if (document.readyState !== "loading") fn();
    else document.addEventListener("DOMContentLoaded", fn, { once: true });
  }

  onReady(function () {
    cursorSpotlight();
    cardSpotlight();
    reveal();
    textReveal();
    marquee();
    headerCondense();
    backToTop();
    ripple();
    window.__motionReady = true;
  });

  /* ---- 1. Cursor spotlight: a lazy lerp that shrinks while moving --- */
  function cursorSpotlight() {
    if (reduce || !window.matchMedia("(pointer: fine)").matches) return;
    var el = document.createElement("div");
    el.id = "cursorSpot";
    el.setAttribute("aria-hidden", "true");
    document.body.appendChild(el);

    var x = window.innerWidth / 2, y = window.innerHeight / 2;
    var tx = x, ty = y, active = false, idle = null;

    function loop() {
      x += (tx - x) * 0.16;
      y += (ty - y) * 0.16;
      el.style.setProperty("--csx", x.toFixed(1) + "px");
      el.style.setProperty("--csy", y.toFixed(1) + "px");
      if (Math.abs(tx - x) > 0.5 || Math.abs(ty - y) > 0.5) {
        requestAnimationFrame(loop);
      } else {
        x = tx; y = ty; active = false;
      }
    }

    window.addEventListener("pointermove", function (e) {
      tx = e.clientX; ty = e.clientY;
      document.body.classList.add("has-spot-cursor");
      el.classList.add("is-moving");
      clearTimeout(idle);
      idle = setTimeout(function () { el.classList.remove("is-moving"); }, 160);
      if (!active) { active = true; requestAnimationFrame(loop); }
    }, { passive: true });

    document.addEventListener("pointerleave", function () {
      document.body.classList.remove("has-spot-cursor");
    });
  }

  /* ---- 2. Card spotlight: one delegated listener sets --mx / --my -- */
  function cardSpotlight() {
    if (reduce) return;
    var selector = ".product-card, .deal-card, .panel, .order-card, .order-item";
    document.querySelectorAll(selector).forEach(function (node) {
      node.classList.add("has-spot");
    });
    var current = null;
    document.addEventListener("pointermove", function (e) {
      var target = e.target && e.target.closest ? e.target.closest(".has-spot") : null;
      current = target;
      if (!target) return;
      var r = target.getBoundingClientRect();
      target.style.setProperty("--mx", (e.clientX - r.left) + "px");
      target.style.setProperty("--my", (e.clientY - r.top) + "px");
    }, { passive: true });
  }

  /* ---- 3. Scroll reveal: explicit + safe below-the-fold reveal -- */
  function reveal() {
    var nodes = Array.prototype.slice.call(document.querySelectorAll("[data-reveal]"));
    if (reduce || !("IntersectionObserver" in window)) {
      document.querySelectorAll("[data-reveal], [data-reveal-text], .auto-reveal").forEach(function (el) {
        el.classList.add("is-in");
      });
      return;
    }

    var io = new IntersectionObserver(function (entries) {
      entries.forEach(function (entry) {
        if (!entry.isIntersecting) return;
        var el = entry.target;
        el.classList.add("is-in");
        io.unobserve(el);
      });
    }, { rootMargin: "0px 0px -7% 0px", threshold: 0.12 });

    // stagger explicit groups
    nodes.forEach(function (node) {
      var parent = node.parentElement;
      var delay = parseInt(node.getAttribute("data-delay") || "0", 10);
      if (parent && parent.hasAttribute("data-reveal-stagger")) {
        var sibs = Array.prototype.filter.call(parent.children, function (c) {
          return c.hasAttribute && c.hasAttribute("data-reveal");
        });
        delay += Math.max(0, sibs.indexOf(node)) * 80;
      }
      node.setAttribute("data-delay", delay);
      node.style.setProperty("--d", delay + "ms");
      io.observe(node);
    });

    // word-by-word text reveals share the same observer
    document.querySelectorAll("[data-reveal-text]").forEach(function (el) { io.observe(el); });

    autoReveal(io);
  }

  /* Auto-reveal: only hide elements that are BELOW the fold right now,
     so above-the-fold content never flashes. A timed failsafe shows
     anything still pending, so nothing can get stuck hidden. */
  function autoReveal(io) {
    var vh = window.innerHeight || html.clientHeight;
    var selector = "main .panel, main .section-title, main .order-card, main .crumb, main .summary-row, main table";
    var pending = [];
    document.querySelectorAll(selector).forEach(function (el) {
      if (el.closest("[data-reveal]") || el.closest(".fade-up") ||
          el.closest(".product-grid") || el.classList.contains("fade-up")) return;
      if (el.getBoundingClientRect().top > vh * 0.86) {
        el.classList.add("auto-reveal");
        io.observe(el);
        pending.push(el);
      }
    });
    if (pending.length) {
      setTimeout(function () {
        pending.forEach(function (el) {
          if (!el.classList.contains("is-in") && el.offsetParent !== null) el.classList.add("is-in");
        });
      }, 6000);
    }
  }

  /* ---- 3b. Word-by-word text reveal ---- */
  function textReveal() {
    var nodes = document.querySelectorAll("[data-reveal-text]");
    if (!nodes.length) return;
    nodes.forEach(function (node) {
      if (node.getAttribute("data-reveal-text-done")) return;
      var words = (node.textContent || "").trim().split(/\s+/).filter(Boolean);
      node.setAttribute("data-reveal-text-done", "1");
      if (!words.length || reduce) return;
      node.setAttribute("aria-label", words.join(" "));
      node.innerHTML = words.map(function (w, i) {
        return '<span class="rw" aria-hidden="true" style="--d:' + (i * 55) + 'ms">' +
          '<i>' + escapeHtml(w) + "</i></span>";
      }).join(" ");
    });
  }

  function escapeHtml(value) {
    return String(value == null ? "" : value).replace(/[&<>"']/g, function (ch) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[ch];
    });
  }

  /* ---- 4. Marquee: duplicate the group for a seamless loop ---- */
  function marquee() {
    if (reduce) return;
    document.querySelectorAll(".marquee").forEach(function (band) {
      var track = band.querySelector(".marquee-track");
      var group = track && track.querySelector(".marquee-group");
      if (!track || !group || track.children.length > 1) return;
      var clone = group.cloneNode(true);
      clone.setAttribute("aria-hidden", "true");
      track.appendChild(clone);
    });
  }

  /* ---- 5. Header condense ---- */
  function headerCondense() {
    var header = document.querySelector(".site-header");
    if (!header) return;
    var update = function () {
      header.classList.toggle("scrolled", (window.pageYOffset || html.scrollTop || 0) > 14);
    };
    window.addEventListener("scroll", update, { passive: true });
    update();
  }

  /* ---- 6. Back to top ---- */
  function backToTop() {
    var btn = document.getElementById("toTop");
    if (!btn) return;
    var update = function () {
      btn.classList.toggle("show", (window.pageYOffset || html.scrollTop || 0) > 480);
    };
    window.addEventListener("scroll", update, { passive: true });
    update();
    btn.addEventListener("click", function () {
      window.scrollTo({ top: 0, behavior: reduce ? "auto" : "smooth" });
    });
  }

  /* ---- 6. Ripple on interactive buttons ---- */
  function ripple() {
    if (reduce) return;
    var selector = ".btn-primary, .btn-ghost, .add-btn, .emi-chip";
    document.addEventListener("pointerdown", function (e) {
      var el = e.target && e.target.closest ? e.target.closest(selector) : null;
      if (!el || el.disabled) return;
      el.classList.add("ripple-host");
      var r = el.getBoundingClientRect();
      var size = Math.max(r.width, r.height);
      var wave = document.createElement("span");
      wave.className = "ripple-wave";
      wave.style.width = wave.style.height = size + "px";
      wave.style.left = (e.clientX - r.left - size / 2) + "px";
      wave.style.top = (e.clientY - r.top - size / 2) + "px";
      el.appendChild(wave);
      setTimeout(function () { wave.remove(); }, 640);
    }, { passive: true });
  }
})();
