/* Composition Marker front end. No framework, no bundler, no external requests. */
(function () {
  "use strict";

  var $ = function (id) { return document.getElementById(id); };

  // `phase` says what the progress bar is currently running, which decides
  // where Cancel goes back to: a cancelled marking has nothing to show, a
  // cancelled correction still has the marked composition behind it.
  var state = {
    jobId: null, events: null, timer: null, started: 0,
    pages: [], docs: {}, tab: null, phase: "mark",
    opts: {}, name: null, dragFrom: null, viewing: null
  };

  /* ---------------------------------------------------------------- boot */

  // run.bat waits for the server before opening the browser, but the page can
  // still be reloaded early or the server can be slow, so this keeps retrying.
  // It reports elapsed time and escalates the wording rather than sitting on a
  // bare spinner: an unexplained "Starting up..." is indistinguishable from a
  // hang, and users reasonably conclude the worse one.
  var bootStarted = Date.now();

  function bootStage(seconds) {
    if (seconds < 10) return ["Contacting the application…", null];
    if (seconds < 30) return ["Still starting. The first launch takes a little longer.", null];
    if (seconds < 90) {
      return ["Still starting.",
              "This is longer than usual. Check the black console window " +
              "titled “Composition Marker” — if it has closed or shows an " +
              "error, close this tab and run run.bat again."];
    }
    return ["The application isn’t responding.",
            "Close this tab, close the black console window, and run run.bat " +
            "again. If it keeps happening, restart the computer — a previous " +
            "run may still be holding the graphics card."];
  }

  function waitForServer(attempt) {
    var seconds = Math.floor((Date.now() - bootStarted) / 1000);
    $("boot-elapsed").textContent = seconds + "s";
    $("boot-bar").style.width = Math.min(90, 8 + seconds * 4) + "%";
    var stage = bootStage(seconds);
    $("boot-msg").textContent = stage[0];
    if (stage[1]) { $("boot-detail").textContent = stage[1]; $("boot-detail").hidden = false; }
    if (seconds >= 90) { $("boot-spinner").hidden = true; $("boot-title").textContent = "Not responding"; }

    fetch("/api/health")
      .then(function (r) { return r.json(); })
      .then(function (h) {
        if (!h.ok) {
          show("connecting", false);
          show("failed", true);
          $("fail-msg").textContent =
            "These files are missing from the application folder:\n\n  " +
            h.missing.join("\n  ") +
            "\n\nThe folder may not have copied completely, or the models " +
            "have not been downloaded yet. Run DOWNLOAD_MODELS.bat, or copy " +
            "the folder again.";
          return;
        }
        $("boot-bar").style.width = "100%";
        show("connecting", false);
        loadOptions();
        reattach();
      })
      .catch(function () {
        setTimeout(function () { waitForServer(attempt + 1); }, 400);
      });
  }

  function show(id, on) { $(id).hidden = !on; }

  /* ------------------------------------------------------------- reattach */

  function reattach() {
    fetch("/api/current")
      .then(function (r) { return r.json(); })
      .then(function (d) {
        if (!d.job) { show("setup", true); loadHistory(); return; }
        state.jobId = d.job.id;
        state.phase = d.job.kind === "correct" ? "correct" : "mark";
        state.name = d.job.name || null;
        show("progress", true);
        $("log").textContent = "";
        state.started = Date.now();
        startClock();
        $("stage-label").textContent = d.job.stage_label || "Working";
        $("pct").textContent = Math.round(d.job.percent) + "%";
        $("bar-fill").style.width = d.job.percent + "%";
        listen();
      })
      .catch(function () { show("setup", true); });
  }

  /* --------------------------------------------------------------- options */

  function loadOptions() {
    fetch("/api/options")
      .then(function (r) { return r.json(); })
      .then(function (d) {
        state.opts = d;

        var lv = $("level");
        lv.textContent = "";
        (d.levels || []).forEach(function (l) {
          var o = document.createElement("option");
          o.value = l.key;
          o.textContent = l.label;
          if (l.key === d.default_level) o.selected = true;
          lv.appendChild(o);
        });

        var lang = $("language");
        lang.textContent = "";
        (d.languages || []).forEach(function (l) {
          var o = document.createElement("option");
          o.value = l.key; o.textContent = l.label;
          if (l.key === d.default_language) o.selected = true;
          lang.appendChild(o);
        });

        // The dropdown defaults to whatever this machine can actually hold,
        // but the choice is the user's: they may know something detection does
        // not.
        var sel = $("model");
        sel.textContent = "";
        (d.models || []).forEach(function (m) {
          var o = document.createElement("option");
          o.value = m.key;
          o.textContent = m.label + (m.available ? "" : "  (not downloaded)");
          o.disabled = !m.available;
          if (m.key === d.recommended) o.selected = true;
          sel.appendChild(o);
        });

        $("port").value = d.port || 9931;
        updateModelChoice();

        var gb = d.vram_mb ? (d.vram_mb / 1024).toFixed(1) + " GB" : "unknown";
        var how = { cuda: "NVIDIA", vulkan: "Vulkan", cpu: "the processor" };
        var note = "Detected " + (d.device || "a graphics card") + " with " + gb +
                   " of video memory, so " +
                   (d.recommended === "q4_k_m" ? "High Quality" : "Low Quality") +
                   " is selected. You can change it.";
        if (d.uma) {
          note += " This machine shares its memory with the graphics chip, so " +
                  "the model runs on the processor — reading each page takes " +
                  "a few minutes rather than a few seconds.";
        } else if (d.backend) {
          note += " Running on " + (how[d.backend] || d.backend) + ".";
        }
        state.detectedNote = note;
        updateModelChoice();
        sel.addEventListener("change", function () {
          updateModelChoice();
          savePreferences();
          showEstimate();
        });
        $("port").addEventListener("change", savePreferences);
        showEstimate();
      })
      .catch(function () { /* the dropdowns stay empty; config still applies */ });
  }

  // The port box only exists when it is relevant, and the note under the
  // dropdown has to stop claiming the graphics card decided anything once the
  // work is being sent somewhere else entirely.
  function updateModelChoice() {
    var external = $("model").value === "port";
    show("port-field", external);
    $("model-note").textContent = external
      ? "The pages and the marking will be sent to the llama-server already " +
        "running on 127.0.0.1, on the port above. Nothing is loaded here, and " +
        "it must be a server that can read images."
      : (state.detectedNote || "");
  }

  // Saved as it is chosen, not when a job starts: choosing Port and typing a
  // number is setup, and losing it by closing the tab would be irritating.
  function savePreferences() {
    fetch("/api/preferences", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ model: $("model").value, port: $("port").value })
    }).catch(function () { /* a remembered choice is a convenience */ });
  }

  function showEstimate() {
    var pages = state.pages.length;
    if (!pages) { $("estimate-note").textContent = ""; return; }
    fetch("/api/estimate?model=" + encodeURIComponent($("model").value) +
          "&pages=" + pages + "&kind=mark")
      .then(function (r) { return r.json(); })
      .then(function (d) {
        // Before the first completed run there is nothing honest to say: the
        // same job varies by more than ten times between a 16GB NVIDIA card
        // and an integrated GPU on the processor.
        if (!d.minutes) {
          $("estimate-note").textContent =
            pages + (pages === 1 ? " page" : " pages") + ". This is the first " +
            "run on this computer with this model, so there is no reliable " +
            "time estimate yet — it will be timed as it goes, and everything " +
            "after this one will be estimated up front.";
          return;
        }
        $("estimate-note").textContent =
          pages + (pages === 1 ? " page" : " pages") + ". Marking will take " +
          "roughly " + d.minutes + (d.minutes === 1 ? " minute" : " minutes") +
          " on this machine. You can leave this window open and come back.";
      })
      .catch(function () { $("estimate-note").textContent = ""; });
  }

  /* ---------------------------------------------------------------- pages */

  var drop = $("drop"), fileInput = $("file");

  drop.addEventListener("click", function () { fileInput.click(); });
  drop.addEventListener("keydown", function (e) {
    if (e.key === "Enter" || e.key === " ") { e.preventDefault(); fileInput.click(); }
  });
  ["dragenter", "dragover"].forEach(function (ev) {
    drop.addEventListener(ev, function (e) { e.preventDefault(); drop.classList.add("over"); });
  });
  ["dragleave", "drop"].forEach(function (ev) {
    drop.addEventListener(ev, function (e) { e.preventDefault(); drop.classList.remove("over"); });
  });
  drop.addEventListener("drop", function (e) {
    if (e.dataTransfer.files && e.dataTransfer.files.length) add(e.dataTransfer.files);
  });
  fileInput.addEventListener("change", function () {
    if (fileInput.files.length) add(fileInput.files);
    fileInput.value = "";
  });

  $("pages-clear").addEventListener("click", function () {
    state.pages.forEach(function (p) { URL.revokeObjectURL(p.url); });
    state.pages = [];
    renderPages();
  });

  // Phone photographs are 4000x3000 and 4MB each. Resizing here rather than on
  // the server keeps `runtime\` free of an imaging library, and matters more
  // than the upload size: the vision encoder charges roughly one token per
  // 28x28 pixels, so a full-resolution page would cost ~15,000 tokens of
  // context to read handwriting that is perfectly legible at 1600px.
  function prepare(file) {
    var maxEdge = state.opts.image_max_edge || 1600;
    var quality = state.opts.image_quality || 0.85;

    function fromBitmap() {
      // imageOrientation: "from-image" applies the EXIF rotation, which is
      // what a photograph taken in portrait needs -- without it every page
      // arrives on its side and the handwriting cannot be read at all.
      return createImageBitmap(file, { imageOrientation: "from-image" })
        .then(function (bmp) {
          var out = draw(bmp, bmp.width, bmp.height, maxEdge, quality);
          bmp.close();
          return out;
        });
    }

    function fromImg() {
      // Fallback for a browser without createImageBitmap options. <img> has
      // applied EXIF orientation itself since Chrome 81.
      return new Promise(function (resolve, reject) {
        var url = URL.createObjectURL(file);
        var img = new Image();
        img.onload = function () {
          var out = draw(img, img.naturalWidth, img.naturalHeight, maxEdge, quality);
          URL.revokeObjectURL(url);
          resolve(out);
        };
        img.onerror = function () { URL.revokeObjectURL(url); reject(new Error("decode")); };
        img.src = url;
      });
    }

    return fromBitmap().catch(fromImg);
  }

  function draw(source, w, h, maxEdge, quality) {
    var scale = Math.min(1, maxEdge / Math.max(w, h));
    var cw = Math.max(1, Math.round(w * scale));
    var ch = Math.max(1, Math.round(h * scale));
    var canvas = document.createElement("canvas");
    canvas.width = cw; canvas.height = ch;
    var ctx = canvas.getContext("2d");
    ctx.imageSmoothingQuality = "high";
    ctx.drawImage(source, 0, 0, cw, ch);
    return new Promise(function (resolve, reject) {
      canvas.toBlob(function (blob) {
        if (blob) resolve({ blob: blob, width: cw, height: ch });
        else reject(new Error("encode"));
      }, "image/jpeg", quality);
    });
  }

  function add(fileList) {
    $("setup-error").hidden = true;
    var max = state.opts.max_pages || 12;
    var files = Array.prototype.slice.call(fileList);
    // Phone galleries hand them over in whatever order the OS felt like, so
    // sorting by filename gets IMG_0041..IMG_0043 the right way round more
    // often than not. The user can still drag them.
    files.sort(function (a, b) { return a.name.localeCompare(b.name, undefined, { numeric: true }); });

    var room = max - state.pages.length;
    if (room <= 0) { fail("That is already " + max + " pages, which is the most this can mark at once."); return; }
    files = files.slice(0, room);

    var pending = files.map(function (file) {
      return prepare(file).then(function (out) {
        state.pages.push({
          blob: out.blob,
          url: URL.createObjectURL(out.blob),
          name: file.name,
          width: out.width,
          height: out.height
        });
      }).catch(function () {
        return file.name;      // resolved with a name means it failed
      });
    });

    $("mark").disabled = true;
    Promise.all(pending).then(function (results) {
      var bad = results.filter(function (r) { return typeof r === "string"; });
      if (bad.length) {
        fail("These couldn’t be read as photographs: " + bad.join(", ") +
             ". If they came from an iPhone they may be HEIC files, which " +
             "Windows browsers cannot open — set the phone to save photos as " +
             "JPEG (Settings, Camera, Formats, Most Compatible) and try again.");
      }
      renderPages();
    });
  }

  function renderPages() {
    // Removing pages revokes their object URLs, so an open viewer could be
    // left pointing at one that no longer decodes.
    if (state.viewing !== null && state.viewing >= state.pages.length) closeViewer();

    var host = $("thumbs");
    host.textContent = "";
    show("pages", state.pages.length > 0);
    $("mark").disabled = state.pages.length === 0;
    $("pages-count").textContent =
      state.pages.length + (state.pages.length === 1 ? " page" : " pages");

    state.pages.forEach(function (page, i) {
      var li = document.createElement("li");
      li.className = "thumb";
      li.draggable = true;
      li.dataset.index = String(i);

      var img = document.createElement("img");
      img.src = page.url;
      img.alt = "Page " + (i + 1);
      img.title = "Double-click to see this page full size";
      // Double-click, not click: a single click is how you pick a thumbnail
      // up to drag it, and opening an overlay on every failed drag would be
      // maddening.
      img.addEventListener("dblclick", function () { openViewer(i); });
      li.appendChild(img);

      var bar = document.createElement("div");
      bar.className = "thumb-bar";

      var no = document.createElement("span");
      no.className = "thumb-no";
      no.textContent = String(i + 1);
      bar.appendChild(no);

      bar.appendChild(arrow("←", "Move earlier", i === 0, function () { move(i, i - 1); }));
      bar.appendChild(arrow("→", "Move later", i === state.pages.length - 1, function () { move(i, i + 1); }));

      var del = arrow("×", "Remove this page", false, function () {
        URL.revokeObjectURL(page.url);
        state.pages.splice(i, 1);
        renderPages();
      });
      del.classList.add("del");
      bar.appendChild(del);
      li.appendChild(bar);

      var note = document.createElement("div");
      note.className = "thumb-note";
      note.textContent = page.width + "×" + page.height;
      li.appendChild(note);

      li.addEventListener("dragstart", function (e) {
        state.dragFrom = i;
        li.classList.add("dragging");
        try { e.dataTransfer.setData("text/plain", String(i)); } catch (err) { /* Firefox needs the call, not the value */ }
        e.dataTransfer.effectAllowed = "move";
      });
      li.addEventListener("dragend", function () {
        li.classList.remove("dragging");
        Array.prototype.forEach.call(host.children, function (c) { c.classList.remove("dropbefore"); });
      });
      li.addEventListener("dragover", function (e) {
        e.preventDefault();
        e.dataTransfer.dropEffect = "move";
        li.classList.add("dropbefore");
      });
      li.addEventListener("dragleave", function () { li.classList.remove("dropbefore"); });
      li.addEventListener("drop", function (e) {
        e.preventDefault();
        li.classList.remove("dropbefore");
        if (state.dragFrom === null) return;
        move(state.dragFrom, i);
        state.dragFrom = null;
      });

      host.appendChild(li);
    });
    showEstimate();
  }

  function arrow(glyph, label, disabled, onClick) {
    var b = document.createElement("button");
    b.type = "button";
    b.className = "thumb-btn";
    b.textContent = glyph;
    b.title = label;
    b.setAttribute("aria-label", label);
    b.disabled = disabled;
    b.addEventListener("click", onClick);
    return b;
  }

  function move(from, to) {
    if (from === to || to < 0 || to >= state.pages.length) return;
    var moved = state.pages.splice(from, 1)[0];
    state.pages.splice(to, 0, moved);
    renderPages();
  }

  function fail(msg) {
    $("setup-error").textContent = msg;
    $("setup-error").hidden = false;
  }

  /* ---------------------------------------------------------- page viewer */

  // Paging inside the overlay matters as much as the zoom: telling page 3 from
  // page 4 means comparing them, and closing and reopening to do that is the
  // slow way round.
  function openViewer(index) {
    if (index < 0 || index >= state.pages.length) return;
    state.viewing = index;
    var page = state.pages[index];
    $("viewer-img").src = page.url;
    $("viewer-img").alt = "Page " + (index + 1);
    $("viewer-title").textContent =
      "Page " + (index + 1) + " of " + state.pages.length;
    $("viewer-prev").disabled = index === 0;
    $("viewer-next").disabled = index === state.pages.length - 1;
    show("viewer", true);
  }

  function closeViewer() {
    show("viewer", false);
    // Release the decoded image rather than leaving a full-size page bitmap
    // held after the overlay is gone.
    $("viewer-img").removeAttribute("src");
    state.viewing = null;
  }

  $("viewer-close").addEventListener("click", closeViewer);
  $("viewer-prev").addEventListener("click", function () { openViewer(state.viewing - 1); });
  $("viewer-next").addEventListener("click", function () { openViewer(state.viewing + 1); });
  $("viewer").addEventListener("click", function (e) {
    if (e.target === $("viewer")) closeViewer();
  });

  /* -------------------------------------------------------------- marking */

  $("mark").addEventListener("click", function () {
    if (!state.pages.length) return;
    $("mark").disabled = true;
    $("setup-error").hidden = true;

    fetch("/api/jobs", { method: "POST" })
      .then(json)
      .then(function (res) {
        if (!res.ok) throw new Error(res.body.error || "Couldn’t start.");
        state.jobId = res.body.job_id;
        return uploadPages(0);
      })
      .then(function () {
        return fetch("/api/jobs/" + state.jobId + "/start", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            level: $("level").value,
            language: $("language").value,
            topic: $("topic").value,
            model: $("model").value,
            port: $("port").value
          })
        }).then(json);
      })
      .then(function (res) {
        if (!res.ok) throw new Error(res.body.error || "Couldn’t start.");
        state.phase = "mark";
        state.tab = null;
        show("setup", false);
        show("history", false);
        show("another", false);
        show("result", false);
        show("progress", true);
        $("log").textContent = "";
        state.started = Date.now();
        startClock();
        listen();
      })
      .catch(function (err) {
        $("mark").disabled = false;
        fail(err.message || "Couldn’t start.");
      });
  });

  // One page at a time, in order, so a failure names the page that failed and
  // the server never has to guess at the sequence.
  function uploadPages(i) {
    if (i >= state.pages.length) return Promise.resolve();
    return fetch("/api/jobs/" + state.jobId + "/pages?index=" + i, {
      method: "POST",
      headers: { "Content-Type": "application/octet-stream" },
      body: state.pages[i].blob
    })
      .then(json)
      .then(function (res) {
        if (!res.ok) throw new Error(res.body.error || "Page " + (i + 1) + " could not be sent.");
        return uploadPages(i + 1);
      });
  }

  function json(r) {
    return r.json()
      .catch(function () { return {}; })
      .then(function (b) { return { ok: r.ok, body: b }; });
  }

  function startClock() {
    stopClock();
    state.timer = setInterval(function () {
      var s = Math.floor((Date.now() - state.started) / 1000);
      $("elapsed").textContent = Math.floor(s / 60) + ":" + ("0" + (s % 60)).slice(-2);
    }, 1000);
  }
  function stopClock() { if (state.timer) { clearInterval(state.timer); state.timer = null; } }

  // Never "about 0s left". A job that has outrun its estimate is still
  // running, and a countdown sitting on zero reads as a stall -- which is
  // exactly what it looked like beside a bar still climbing through 73%.
  function remainingText(sec) {
    if (sec === null || sec === undefined) return "";
    if (sec < 20) return "finishing up";
    if (sec < 60) return "less than a minute left";
    return "about " + humanClock(sec) + " left";
  }

  function humanClock(sec) {
    sec = Math.round(sec);
    var h = Math.floor(sec / 3600), m = Math.floor((sec % 3600) / 60);
    if (h > 0) return h + "h " + m + "m";
    if (m > 0) return m + "m " + (sec % 60) + "s";
    return sec + "s";
  }

  function listen() {
    if (state.events) state.events.close();
    var es = new EventSource("/api/events/" + state.jobId);
    state.events = es;

    es.onmessage = function (e) {
      var ev;
      try { ev = JSON.parse(e.data); } catch (err) { return; }

      if (ev.type === "status") {
        // Trust the server's elapsed over the local clock: after a reload the
        // page has no idea when the job actually started.
        if (ev.elapsed) state.started = Date.now() - ev.elapsed * 1000;
        $("stage-label").textContent = ev.stage_label || "Working";
        $("stage-msg").textContent = ev.message || "";
        $("pct").textContent = Math.round(ev.percent) + "%";
        $("bar-fill").style.width = ev.percent + "%";
        $("remaining").textContent = remainingText(ev.remaining);
      } else if (ev.type === "log") {
        var box = $("log");
        var atBottom = box.scrollHeight - box.scrollTop - box.clientHeight < 40;
        box.textContent += ev.line + "\n";
        if (atBottom) box.scrollTop = box.scrollHeight;
      } else if (ev.type === "done") {
        es.close(); stopClock();
        var wasCorrection = state.phase === "correct";
        state.phase = "mark";
        openResult(wasCorrection);
      } else if (ev.type === "error") {
        es.close(); stopClock();
        show("progress", false);
        show("failed", true);
        $("fail-msg").textContent = ev.message;
      } else if (ev.type === "cancelled") {
        es.close(); stopClock();
        show("progress", false);
        if (state.phase === "correct") {
          // Nothing was lost: the marked composition behind this is still
          // there, so go back to it rather than to an empty form.
          state.phase = "mark";
          openResult(false);
        } else {
          show("setup", true);
          $("mark").disabled = state.pages.length === 0;
          state.jobId = null;
          loadHistory();
        }
      }
    };
  }

  $("cancel").addEventListener("click", function () {
    $("cancel").disabled = true;
    fetch("/api/jobs/" + state.jobId + "/cancel", { method: "POST" })
      .finally(function () { $("cancel").disabled = false; });
  });

  /* --------------------------------------------------------------- result */

  // Shared by a finished marking, a reopened composition from the history
  // list, and the end of a correction -- all three land on the same page.
  function openResult(openFolderAfter) {
    fetch("/api/jobs/" + state.jobId + "/result")
      .then(function (r) { return r.json(); })
      .then(function (d) {
        state.docs = d.documents || {};
        state.name = d.name;
        show("progress", false);
        show("failed", false);
        show("setup", false);
        show("history", false);
        show("result", true);
        show("another", true);
        $("build-error").hidden = true;

        $("score").textContent = (d.score === null || d.score === undefined)
          ? "—" : d.score + " / 100";
        var sub = [];
        if (d.level_label) sub.push("Singapore " + d.level_label);
        if (d.topic) sub.push("“" + d.topic + "”");
        if (d.score === null || d.score === undefined) {
          sub.push("no mark could be read from the report");
        }
        $("score-sub").textContent = sub.join(" · ");

        // Transcript first: it is what the marking is an opinion about, and
        // the one thing worth checking against the page before anything else
        // is believed.
        var keys = ["transcript", "marking", "minimal", "improved"]
          .filter(function (k) { return !!state.docs[k]; });
        var tabs = $("tabs");
        tabs.hidden = keys.length < 2;
        Array.prototype.forEach.call(tabs.querySelectorAll(".tab"), function (t) {
          t.hidden = keys.indexOf(t.dataset.target) === -1;
        });
        // Transcript sits first in the row -- it is what the marking is an
        // opinion about -- but the marking is what the user came for, so that
        // is the tab that opens. `state.tab` wins when it is set, which is how
        // a finished correction lands on the document it just wrote.
        if (keys.length) {
          var wanted = keys.indexOf(state.tab) !== -1 ? state.tab
                     : keys.indexOf("marking") !== -1 ? "marking"
                     : keys[0];
          select(wanted);
        }
        buildNote();
        updateGenerateLabel();

        if (openFolderAfter) openFolder();
      });
  }

  function buildNote() {
    var pages = 1;
    fetch("/api/estimate?model=" + encodeURIComponent($("model").value) +
          "&pages=" + pages + "&kind=correct&corrections=" +
          ($("correction").value === "both" ? 2 : 1))
      .then(function (r) { return r.json(); })
      .then(function (d) {
        $("build-note").textContent = d.minutes
          ? "About " + d.minutes + (d.minutes === 1 ? " minute" : " minutes") +
            " — the model generates the corrected version from the transcript."
          : "The model generates the corrected version from the transcript.";
      })
      .catch(function () { $("build-note").textContent = ""; });
  }

  $("correction").addEventListener("change", function () {
    buildNote();
    updateGenerateLabel();
  });

  $("generate").addEventListener("click", function () {
    if (!state.jobId) return;
    $("build-error").hidden = true;
    fetch("/api/jobs/" + state.jobId + "/correct", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ correction: $("correction").value,
                             model: $("model").value,
                             port: $("port").value })
    })
      .then(json)
      .then(function (res) {
        if (!res.ok) {
          $("build-error").textContent = res.body.error || "That couldn’t be started.";
          $("build-error").hidden = false;
          return;
        }
        state.phase = "correct";
        state.tab = $("correction").value === "improved" ? "improved" : "minimal";
        show("result", false); show("another", false);
        show("progress", true);
        $("log").textContent = "";
        state.started = Date.now();
        startClock();
        listen();
      });
  });

  // Everything a run produces is already saved in the composition's own
  // folder, so a download would only put a second copy in Downloads\. Opening
  // the folder puts all four documents in front of the user at once, which is
  // where they will want to be anyway to print or send them.
  function openFolder() {
    fetch("/api/open-output", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name: state.name })
    });
  }

  $("open-folder").addEventListener("click", openFolder);

  // "Generate" the first time, "Regenerate" once the files exist -- so the
  // button says whether pressing it will cost a wait for something new or
  // overwrite something already there.
  function updateGenerateLabel() {
    var want = $("correction").value;
    var kinds = want === "both" ? ["minimal", "improved"] : [want];
    var have = kinds.every(function (k) { return !!state.docs[k]; });
    $("generate").textContent = have ? "Regenerate" : "Generate";
  }

  $("again").addEventListener("click", function () {
    state.pages.forEach(function (p) { URL.revokeObjectURL(p.url); });
    state.pages = [];
    state.jobId = null;
    state.docs = {};
    state.tab = null;
    renderPages();
    $("topic").value = "";
    show("result", false);
    show("another", false);
    show("failed", false);
    show("setup", true);
    loadHistory();
    window.scrollTo(0, 0);
  });

  $("retry").addEventListener("click", function () {
    show("failed", false);
    show("setup", true);
    $("mark").disabled = state.pages.length === 0;
  });

  $("copy-diag").addEventListener("click", function () {
    fetch("/api/diagnostics")
      .then(function (r) { return r.text(); })
      .then(function (t) {
        return navigator.clipboard.writeText(t).then(function () {
          $("copy-diag").textContent = "Copied";
          setTimeout(function () { $("copy-diag").textContent = "Copy diagnostic info"; }, 2000);
        });
      })
      .catch(function () { $("copy-diag").textContent = "Couldn’t copy"; });
  });

  /* ----------------------------------------------------------------- tabs */

  Array.prototype.forEach.call(document.querySelectorAll(".tab"), function (tab) {
    tab.addEventListener("click", function () { select(tab.dataset.target); });
  });

  function select(key) {
    state.tab = key;
    Array.prototype.forEach.call(document.querySelectorAll(".tab"), function (t) {
      t.classList.toggle("active", t.dataset.target === key);
    });
    var d = state.docs[key];
    // The marking report is a document with headings and tables. The other
    // three are the composition itself, and are shown as the child's own text
    // -- rendering them as Markdown would silently eat a line beginning with a
    // dash or a number, which in a story is a line of dialogue.
    if (!d) { $("doc").textContent = ""; return; }
    if (key === "marking") {
      $("doc").innerHTML = renderMarkdown(d.markdown);
    } else {
      $("doc").innerHTML = renderComposition(d.markdown);
    }
  }

  /* ------------------------------------------------------------- history */

  function loadHistory() {
    fetch("/api/history")
      .then(function (r) { return r.json(); })
      .then(function (d) {
        var list = d.compositions || [];
        var host = $("hist-list");
        host.textContent = "";
        if (!list.length) { show("history", false); return; }
        list.forEach(function (c) { host.appendChild(historyRow(c)); });
        show("history", true);
      })
      .catch(function () { show("history", false); });
  }

  $("hist-refresh").addEventListener("click", loadHistory);

  function historyRow(c) {
    var row = document.createElement("button");
    row.className = "hist-row";
    row.type = "button";

    var name = document.createElement("span");
    name.className = "hist-name";
    name.textContent = c.name;
    row.appendChild(name);

    // The level, and nothing else. A mark out of 100 on a list of a child's
    // work reads as a league table, and whether a corrected version happens to
    // exist yet is not a property of the composition worth listing.
    var meta = document.createElement("span");
    meta.className = "hist-meta";
    var bits = [];
    if (c.level_label) bits.push(c.level_label);
    if (c.language === "zh") bits.push("Chinese");
    meta.textContent = bits.join(" · ");
    row.appendChild(meta);

    row.addEventListener("click", function () { openHistory(c.name, row); });
    return row;
  }

  function openHistory(name, row) {
    row.disabled = true;
    fetch("/api/history/open", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name: name })
    })
      .then(json)
      .then(function (res) {
        row.disabled = false;
        if (!res.ok) { loadHistory(); return; }
        state.jobId = res.body.job_id;
        state.tab = null;
        openResult(false);
        window.scrollTo(0, 0);
      })
      .catch(function () { row.disabled = false; });
  }

  /* ------------------------------------------------------------- markdown */

  function esc(s) {
    return s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
  }

  function inline(s) {
    return esc(s)
      .replace(/`([^`]+)`/g, "<code>$1</code>")
      .replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>")
      .replace(/(^|[^*])\*([^*]+)\*/g, "$1<em>$2</em>");
  }

  function isRow(line) {
    return typeof line === "string" && /^\s*\|.*\|\s*$/.test(line);
  }

  // Once a table has started, a line that opens with a pipe belongs to it even
  // if the closing pipe is missing. The model drops the trailing pipe often
  // enough that a table was seen rendering its first rows and then spilling
  // the rest onto the page as raw text.
  function isBodyRow(line) {
    return typeof line === "string" && /^\s*\|/.test(line);
  }

  // The ---|---|--- line under the header. Colons for alignment are accepted
  // and ignored: nothing the prompts ask for depends on them.
  function isDivider(line) {
    return typeof line === "string" && /^\s*\|[\s:|-]+\|\s*$/.test(line) &&
           line.indexOf("-") !== -1;
  }

  function cells(line) {
    var t = line.trim().replace(/^\|/, "").replace(/\|$/, "");
    return t.split("|").map(function (c) { return c.trim(); });
  }

  // The table is written by the model, so it is not always well formed: a run
  // was observed emitting a three-column header above four-column rows.
  // Widening to the widest row keeps the extra cells visible rather than
  // silently dropping a column of the marking.
  function table(head, body) {
    var cols = head.length;
    body.forEach(function (row) { if (row.length > cols) cols = row.length; });

    // The criteria table is the one with short numeric columns that must not
    // wrap -- "26 / 40" broken over two lines is unreadable. Detected by its
    // heading rather than styled globally, because column two of the sentence
    // table is a whole sentence and must be free to wrap.
    var marks = head.length > 2 && /^criterion$/i.test((head[0] || "").trim());
    var html = '<div class="tablewrap"><table' + (marks ? ' class="marks"' : '') +
               '><thead><tr>';
    for (var h = 0; h < cols; h++) {
      html += "<th>" + inline(head[h] === undefined ? "" : head[h]) + "</th>";
    }
    html += "</tr></thead><tbody>";
    body.forEach(function (row) {
      html += "<tr>";
      for (var c = 0; c < cols; c++) {
        html += "<td>" + inline(row[c] === undefined ? "" : row[c]) + "</td>";
      }
      html += "</tr>";
    });
    return html + "</tbody></table></div>";
  }

  // Just enough Markdown for what the marking prompt emits: headings, bullets,
  // rules, bold, and the two tables that carry the score and the sentence
  // rewrites.
  function renderMarkdown(md) {
    var out = [], list = null;
    var lines = md.split(/\r?\n/);

    function closeList() { if (list) { out.push("</" + list + ">"); list = null; } }

    for (var i = 0; i < lines.length; i++) {
      var line = lines[i];

      if (isRow(line) && isDivider(lines[i + 1])) {
        closeList();
        var head = cells(line);
        var body = [];
        i += 2;
        while (i < lines.length && isBodyRow(lines[i])) { body.push(cells(lines[i])); i++; }
        i--;
        out.push(table(head, body));
        continue;
      }

      var h = line.match(/^(#{1,4})\s+(.*)$/);
      if (h) {
        closeList();
        out.push("<h" + h[1].length + ">" + inline(h[2]) + "</h" + h[1].length + ">");
        continue;
      }

      if (/^---+\s*$/.test(line)) { closeList(); out.push("<hr>"); continue; }

      var bullet = line.match(/^\s*[-*]\s+(.*)$/);
      if (bullet) {
        if (list !== "ul") { closeList(); out.push("<ul>"); list = "ul"; }
        out.push("<li>" + inline(bullet[1]) + "</li>");
        continue;
      }

      var num = line.match(/^\s*\d+\.\s+(.*)$/);
      if (num) {
        if (list !== "ol") { closeList(); out.push("<ol>"); list = "ol"; }
        out.push("<li>" + inline(num[1]) + "</li>");
        continue;
      }

      if (line.trim() === "") { closeList(); continue; }

      closeList();
      out.push("<p>" + inline(line) + "</p>");
    }
    closeList();
    return out.join("\n");
  }

  // The composition documents carry a small header block and then the child's
  // text. Only the header is Markdown; the text is shown exactly as written.
  function renderComposition(md) {
    var parts = md.split(/\n---\n/);
    var head = parts.length > 1 ? parts[0] : "";
    var body = parts.length > 1 ? parts.slice(1).join("\n---\n") : md;
    return (head ? renderMarkdown(head) : "") +
           '<div class="composition">' + esc(body.trim()) + "</div>";
  }

  /* ----------------------------------------------------------------- about */

  function openAbout() {
    show("about", true);
    fetch("/api/about")
      .then(function (r) { return r.json(); })
      .then(function (d) {
        $("about-author").textContent = d.author || "";
        $("about-version").textContent = d.version || "";
        var link = $("about-repo");
        link.textContent = (d.repo_url || "").replace(/^https:\/\//, "");
        link.href = d.repo_url || "#";
      })
      .catch(function () { $("about-version").textContent = "unknown"; });
  }

  $("about-open").addEventListener("click", openAbout);
  $("about-close").addEventListener("click", function () { show("about", false); });
  $("about").addEventListener("click", function (e) {
    if (e.target === $("about")) show("about", false);
  });
  document.addEventListener("keydown", function (e) {
    if (e.key === "Escape" && !$("about").hidden) show("about", false);
    if ($("viewer").hidden) return;
    if (e.key === "Escape") closeViewer();
    if (e.key === "ArrowLeft") openViewer(state.viewing - 1);
    if (e.key === "ArrowRight") openViewer(state.viewing + 1);
  });

  waitForServer(0);
})();
