"use strict";
/* Clip-review loop: queue -> player -> schema form -> save & next.
 * Enums and required-ness come from the server-published JSON Schema so the
 * schema file stays the single source of truth; layout, labels, and
 * keyboard behavior live here. */

const $ = id => document.getElementById(id);
const token = new URLSearchParams(location.search).get("token") || "";
const api = path => path + (path.includes("?") ? "&" : "?") + "token=" + encodeURIComponent(token);
const FPS = 30;

// Open vocabulary — these are anchors, not options. Keep the polarity
// balanced: a negative-only list would skew the corpus's read vocabulary
// toward the dramatic half of the register space (real-use finding,
// ADC-011).
const SUGGEST = {
  affect_observed: ["angry", "frightened", "dismissive", "flat", "tearful",
                    "embarrassed", "skeptical", "overwhelmed",
                    "calm", "relieved", "cheerful", "engaged", "trusting",
                    "curious", "hopeful", "stoic"],
  register_selected: ["brisk", "slow", "warm", "firm", "playful", "grave", "matter-of-fact"],
};

let schema = null;
let queue = [];
let idx = 0;
let markIn = null, markOut = null;
const v = $("v");

// --- form built from the schema ---------------------------------------------

function enumOf(props, name) { return props[name].enum; }

function buildForm() {
  const p = schema.properties;
  const read = p.read.properties;
  const move = p.move.properties;
  const el = html => { const t = document.createElement("template"); t.innerHTML = html.trim(); return t.content.firstChild; };
  const select = (id, values, required) =>
    `<select id="${id}" ${required ? "required" : ""}><option value="">—</option>` +
    values.map(o => `<option>${o}</option>`).join("") + `</select>`;
  const datalist = (id, values) =>
    `<datalist id="${id}">` + values.map(o => `<option>${o}</option>`).join("") + `</datalist>`;

  $("formBody").append(
    el(`<fieldset><legend>Encounter</legend><div class="grid">
      <div><label for="session_id">Session id</label><input type="text" id="session_id" required></div>
      <div><label for="segment_class">Segment class</label>${select("segment_class", enumOf(p, "segment_class"), true)}</div>
      </div>
      <label for="context">Context <span class="opt">— de-identified; minimal setting needed to understand the read</span></label>
      <textarea id="context"></textarea>
    </fieldset>`),
    el(`<fieldset><legend>The read — what you were seeing at that moment</legend><div class="grid">
      <div><label for="acuity">Acuity</label>${select("acuity", enumOf(read, "acuity"), true)}</div>
      <div><label for="affect_observed">Affect observed</label>
        <input type="text" id="affect_observed" list="affects" required>${datalist("affects", SUGGEST.affect_observed)}</div>
      <div><label for="prior_relationship">Prior relationship</label>${select("prior_relationship", enumOf(read, "prior_relationship"), true)}</div>
      <div><label for="register_selected">Register selected</label>
        <input type="text" id="register_selected" list="registers" required>${datalist("registers", SUGGEST.register_selected)}</div>
      </div>
      <p class="opt" style="margin:.4rem 0 0">Register = the whole delivery mode selected, not just voice:
        prosody (pace, volume, pitch), pacing and pauses, stance/nonverbals, and word choice.
        It is the thing that changes when the read changes.</p>
      <label for="why">Why <span class="opt">— what in the patient's presentation drove the register choice (the payload)</span></label>
      <textarea id="why" required></textarea>
    </fieldset>`),
    el(`<fieldset><legend>The move</legend>
      <label for="move_description">Description <span class="opt">— what the clinician did communicatively and what it was going for</span></label>
      <textarea id="move_description" required></textarea>
      <label for="discriminating_feature">Discriminating feature <span class="opt">— what made this the right call rather than a plausible alternative</span></label>
      <textarea id="discriminating_feature" required></textarea>
      <label for="in_frame_response">In-frame response <span class="opt">— observable patient shift after the move, if any</span></label>
      <textarea id="in_frame_response"></textarea>
    </fieldset>`),
    el(`<fieldset><legend>Self-rating — failures are required corpus content</legend><div class="grid">
      <div><label>Did the move work?</label><div class="radio-row">
        <label><input type="radio" name="worked" value="1" required> worked</label>
        <label><input type="radio" name="worked" value="0"> failed</label></div></div>
      <div><label for="confidence">Confidence: <span id="confval">0.7</span></label>
        <input type="range" id="confidence" min="0" max="1" step="0.05" value="0.7"
          oninput="document.getElementById('confval').textContent=this.value"></div>
      </div>
    </fieldset>`),
    el(`<fieldset class="gap"><legend>Schema gap</legend>
      <label for="schema_gap">What mattered here that the fields above could <em>not</em> capture?
        <span class="opt">Write "nothing" only if that is true.</span></label>
      <textarea id="schema_gap"></textarea>
    </fieldset>`)
  );
  void move; // move fields are laid out above; the schema drives validation server-side
}

// --- queue navigation --------------------------------------------------------

async function loadQueue(keepIndex = false) {
  queue = await (await fetch(api("/api/queue"))).json();
  if (!queue.length) { $("clipLabel").textContent = "no clips found"; return; }
  if (!keepIndex) {
    const firstOpen = queue.findIndex(q => q.records === 0);
    idx = firstOpen === -1 ? 0 : firstOpen;
  }
  showClip();
}

function showClip() {
  const item = queue[idx];
  markIn = null; markOut = null;
  $("clipLabel").textContent = `${item.file} (${idx + 1}/${queue.length})`;
  const done = queue.filter(q => q.records > 0).length;
  $("progress").textContent = `${done}/${queue.length} clips annotated · this clip: ${item.records} record(s)`;
  v.src = api("/api/media/" + encodeURIComponent(item.file));
  $("session_id").value = item.file.replace(/\.[^.]+$/, "");
  renderBounds();
}

$("prevClip").onclick = () => { idx = (idx - 1 + queue.length) % queue.length; showClip(); };
$("nextClip").onclick = () => { idx = (idx + 1) % queue.length; showClip(); };
$("exportLink").onclick = e => { e.preventDefault(); window.open(api("/api/export")); };

// --- player -----------------------------------------------------------------

function renderBounds() {
  const fmt = t => t === null ? "–" : t.toFixed(2) + "s";
  $("clipBounds").textContent = `in ${fmt(markIn)} · out ${fmt(markOut)}`;
}

function rvfcLoop(_now, meta) {
  $("timeReadout").textContent =
    `${meta.mediaTime.toFixed(2)}s · frame ${Math.round(meta.mediaTime * FPS - 0.5)}`;
  const frac = v.duration ? meta.mediaTime / v.duration : 0;
  document.querySelector("#scrub .tick").style.left = `calc(${(frac * 100).toFixed(3)}% - 1px)`;
  document.querySelector("#scrub .fill").style.width = `${(frac * 100).toFixed(3)}%`;
  v.requestVideoFrameCallback(rvfcLoop);
}
if ("requestVideoFrameCallback" in HTMLVideoElement.prototype) v.requestVideoFrameCallback(rvfcLoop);

const scrub = $("scrub");
let dragging = false;
function scrubTo(clientX) {
  const rect = scrub.getBoundingClientRect();
  const frac = Math.min(Math.max((clientX - rect.left) / rect.width, 0), 1);
  if (v.duration) v.currentTime = frac * v.duration;
}
scrub.addEventListener("pointerdown", e => { dragging = true; scrub.setPointerCapture(e.pointerId); v.pause(); scrubTo(e.clientX); });
scrub.addEventListener("pointermove", e => { if (dragging) scrubTo(e.clientX); });
scrub.addEventListener("pointerup", () => { dragging = false; });

function seekFrames(delta) {
  const frameIdx = Math.round(v.currentTime * FPS - 0.5) + delta;
  v.pause();
  v.currentTime = Math.min(Math.max((frameIdx + 0.5) / FPS, 0), (v.duration || 0) - 0.5 / FPS);
}

document.addEventListener("keydown", e => {
  const typing = /^(INPUT|TEXTAREA|SELECT)$/.test(e.target.tagName);
  if ((e.metaKey || e.ctrlKey) && e.key === "Enter") { e.preventDefault(); $("f").requestSubmit(); return; }
  if (typing) return;
  switch (e.key) {
    case " ": e.preventDefault(); v.paused ? v.play() : v.pause(); break;
    case "ArrowRight": e.preventDefault(); seekFrames(e.shiftKey ? FPS : 1); break;
    case "ArrowLeft": e.preventDefault(); seekFrames(e.shiftKey ? -FPS : -1); break;
    case "i": case "I": markIn = v.currentTime; renderBounds(); break;
    case "o": case "O": markOut = v.currentTime; renderBounds(); break;
    case "m": case "M": v.muted = !v.muted; break;
    case "[": $("prevClip").click(); break;
    case "]": $("nextClip").click(); break;
  }
});

// --- save -------------------------------------------------------------------

function clipBounds() {
  // Selection is the harness's whole point: never silently claim the whole
  // file. No marks -> refuse; I only -> single-frame clip at the mark;
  // I+O -> the segment.
  if (markIn === null) return null;
  const t_end = markOut !== null && markOut > markIn ? markOut : markIn + 1 / FPS;
  const r = t => Math.round(t * 1000) / 1000;
  return { t_start: r(markIn), t_end: r(t_end) };
}

function buildRecord() {
  const val = id => $(id).value.trim();
  const record = {
    schema_version: "0.1.0",
    record_id: crypto.randomUUID(),
    session_id: val("session_id"),
    capture: "recording",
    clip: clipBounds(),
    segment_class: val("segment_class"),
    read: {
      acuity: val("acuity"),
      affect_observed: val("affect_observed"),
      prior_relationship: val("prior_relationship"),
      register_selected: val("register_selected"),
      why: val("why"),
    },
    move: {
      description: val("move_description"),
      discriminating_feature: val("discriminating_feature"),
    },
    self_rating: {
      worked: Number(document.querySelector("input[name=worked]:checked").value),
      confidence: Number($("confidence").value),
    },
    annotated_at: new Date().toISOString(),
  };
  for (const [field, id] of [["context", "context"], ["in_frame_response", "in_frame_response"], ["schema_gap", "schema_gap"]]) {
    const text = val(id);
    if (text) record[field] = text;
  }
  return record;
}

$("f").addEventListener("submit", async e => {
  e.preventDefault();
  const status = $("status");
  const record = buildRecord();
  if (!record.clip) {
    status.textContent = "mark the moment first: I at the clip start (O for its end), then save";
    status.className = "err";
    return;
  }
  const payload = { clip_file: queue[idx].file, record };
  const res = await fetch(api("/api/records"), {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  if (res.ok) {
    status.textContent = "saved";
    status.className = "ok";
    e.target.reset();
    $("confval").textContent = "0.7";
    await loadQueue();               // advances to the next unannotated clip
    $("segment_class").focus();
  } else {
    const body = await res.json().catch(() => ({}));
    const detail = Array.isArray(body.detail)
      ? body.detail.map(d => `${d.path}: ${d.message}`).join("\n")
      : JSON.stringify(body.detail || body);
    status.textContent = "rejected by schema:\n" + detail;
    status.className = "err";
  }
  setTimeout(() => { if (status.className === "ok") status.textContent = ""; }, 2500);
});

// --- boot -------------------------------------------------------------------

(async () => {
  schema = await (await fetch(api("/api/schema"))).json();
  buildForm();
  await loadQueue();
})();
