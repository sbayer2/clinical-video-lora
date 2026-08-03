# Clinical Encounter Video — Action Plan v0.2

*The "Encounter Delivery Harness" throughout this document and the ADC log.*

**Author context:** prepared by a practicing urgent care clinician-developer, from a working session that empirically established the core technical premise. Urgent care is the author's setting and the origin of the question; nothing in the plan is specific to it.
**Purpose:** a decision map with the irreversible choices flagged — not a spec.
**Status (2026-07-28):** Phase 0 legal predicates remain **unresolved and still gate all capture**; an IRB-governed academic partnership is the route now being pursued to resolve them (see 0.6). Phase 1–4 tooling is built, and the conditioning pathway has been tested end to end on an archival proxy corpus — producing a **measured null** on the central claim, with one earlier positive result retracted on multi-seed replication (ADC-012..015; summary in `../README.md`). Section numbering is unchanged from v0.1, since ADC entries cite it.

---

## 0. Thesis

In a great many clinical encounters the plan is largely protocol. Delivery is not. Adherence tracks communication quality more strongly than most of what clinicians argue about (Zolnierek & DiMatteo 2009: ~1.5–2x odds of non-adherence with poor communicators). Therefore the scarce, non-commodity asset in a given clinician is **delivery conditioned on clinical read** — and it is currently discarded by every ambient-scribe pipeline in the market (Abridge, DAX, Suki all keep the documentation and throw the interaction away).

Urgent care, the author's setting, makes the protocol/delivery split unusually stark and is where the question originated. It is not the boundary of the claim: primary care, where continuity makes delivery cumulative across a relationship, is if anything the stronger case.

The harness captures that, structures it, and aims to produce a **style adapter** for a future multimodal any-to-any / latent-predictive model. The model supplies clinical reasoning. The adapter supplies delivery.

**Status of the thesis (2026-07-28).** The capture-and-annotate half is built and works. The adapter half is unproven: three training cycles on archival film have not produced register conditioning, and the one positive result was retracted when it failed to replicate across seeds. Two things follow. First, the thesis is a hypothesis under test, not a finding, and should be cited as such. Second, the **annotated corpus is the deliverable that does not depend on the thesis being right** — labeled encounter video is useful to clinical AI research (communication assessment, resident education, practice-pattern description) whether or not an adapter ever learns to modulate register from it.

**Target horizon:** ~5 years. Architecture unknown. This implies capture at maximum fidelity and annotation in an architecture-neutral schema.

---

## 1. What this session established empirically

These findings are load-bearing for every design decision below. They were derived by hand on a public dataset (`mlx-community/medfit-dataset`, 5,155 train rows) and a LoRA run on `Qwen3-8B-4bit`.

| Finding | Evidence |
|---|---|
| LoRA transfers **form**, not **content** | 4.85M trainable params (0.059%). Adapter reformatted output; installed no new facts. |
| The corpus was empty at the actionable level | 338 screening mentions, **zero** canonical screening ages (35/40/45/50/65/75). "Numbers" in screening passages were 255/300 unicode punctuation escapes (`\u2019`, `\u2014`, `\u2013`). |
| Style transfer is **not** content-neutral | Adapter promoted a stale ADA threshold (45; correct is 35 since 2022 SoC) from buried reasoning to bolded item #1, and fabricated a "50%" risk statistic the base model never produced. |
| The dataset's own eval measured style and reported it as medical improvement | README headline metrics: "direct answer capability 6%→36%", "18% increase in organized formatting". No accuracy-vs-ground-truth number anywhere. "Manual review and validation of medical accuracy" passed because nothing was specific enough to be false. |
| This is a known result | LIMA (Zhou et al. 2023), superficial alignment hypothesis: knowledge is acquired in pretraining; alignment tuning surfaces latent format and persona. |
| It recurs across architectures | Cf. own finding F11 (Qwen-AgentWorld transferred world-modeling *form* but not physics *content*). Architecture change is not immunity. |

**Design consequence:** a harness that captures only the surface of encounters will produce an adapter that reproduces the *appearance* of skilled delivery. That is the medfit failure at higher resolution. Everything in Phase 2 exists to prevent it.

---

## 2. Phase 0 — Blocking predicates (resolve before any capture)

Do not build ingestion around data you may not be able to keep.

**0.1 Data ownership.** Where the recording clinician practices as an independent contractor, the covered entity is almost certainly the facility, not the clinician. Patient authorization and data ownership are *separate questions* — consent does not transfer rights in the recording to a contractor. Resolve in writing: who owns encounter recordings made on facility premises, and under what terms may they leave. This may be dispositive. Get healthcare counsel; this document is not legal advice.

**0.2 De-identification is not available.** Full-face imagery is an enumerated Safe Harbor identifier; voice is biometric. The data *is* the identifier — you cannot strip it and retain what you wanted. Plan for permanent authorization + IRB, not a one-time cost.

**0.3 Discoverability.** Every recording is discoverable. Ask the malpractice carrier before, not after. Expect resistance. Have an answer on retention limits and access control.

**0.4 IRB pathway.** Research protocol with prospective consent. Decide early whether the protocol covers model training and downstream deployment, or only corpus construction — retrofitting consent scope is expensive.

**0.5 Consent instrument.** Layered: (a) recording, (b) research use, (c) model training, (d) style reproduction in a disclosed AI system. Patients must be able to grant (a)–(b) and decline (c)–(d). Assume a meaningful decline rate and design the corpus around it.

**0.6 Institutional partnership path (added v0.2).** 0.1–0.5 are written for the solo case: an individual clinician recording in a facility where he is a contractor. An academic research partnership does not remove those questions, it changes who answers them. The partnering institution is the covered entity, its IRB governs the protocol, and it holds the resulting video and derived annotations. **0.1** (ownership) and **0.4** (IRB pathway) are then settled by the institution's existing arrangements rather than negotiated per-clinician; **0.2** (de-identification unavailable — full face and voice are the identifier) and **0.5** (layered consent) are unchanged and still binding; **0.3** (discoverability) shifts to the institution's counsel and carrier.

What the partnership *adds* is a second subject population. If the encounters are resident clinic visits, residents are subjects too: their consent is a separate instrument from the patients', and their recorded encounters must be firewalled from performance evaluation. This is not only an ethics requirement — recording perceived as assessment changes the behavior under study and depresses participation, which by section 8.7's own logic attacks corpus size at the supply end.

The empirical requirements the archival pilot established should be written into any protocol at the outset: **per-speaker audio tracks** (section 3 already specifies separate lapel channels; ADC-013/015 showed why — single-track audio made adapters speak the patient's lines), and **counterfactual sampling** — comparable presentations delivered in different registers, which a multi-clinician teaching clinic supplies naturally but only if sampled for deliberately.

> **Gate:** no capture hardware is purchased until 0.1 and 0.3 return usable answers — from whichever path, solo or institutional, is actually taken.

---

## 3. Phase 1 — Capture rig (irreversible decisions)

These choices are permanent. A corpus captured wrong is not recoverable by any future model quality.

| Decision | Specification | Why irreversible |
|---|---|---|
| Audio channels | **Separate lapel tracks per speaker.** Never room-mic-only. | Speaker separation is trivial at capture, near-impossible in post. Any-to-any training needs it. |
| Audio format | 48 kHz, 24-bit, uncompressed or lossless (FLAC/WAV) | Prosody is the payload. Lossy codecs discard it first. |
| Sync | Hardware timecode across all streams (LTC or genlock) | >100 ms drift destroys gesture–prosody alignment, which is the correlation you are collecting. |
| Camera | Two angles minimum: clinician-facing and patient-facing | A single fixed camera loses one face. Both carry signal. |
| Video | ≥1080p, ≥30 fps, high-bitrate intra-frame codec (ProRes/DNxHR) if storage allows | Micro-expression and gaze are sub-second. Long-GOP compression smears them. |
| Room | Fixed geometry, consistent lighting, documented | Reduces nuisance variance the model would otherwise have to explain. |
| Clock | Single monotonic session clock, all annotations reference it | The join key for everything in Phase 2. |

**Storage math:** budget for it before committing. Two ProRes streams + multitrack lossless audio ≈ 100–200 GB/hour. At 20 encounters/shift you are into petabyte territory fast — which is a real argument for **selective retention** (§4) rather than record-everything.

**Build now:** a capture-validation script that verifies sync, channel separation, levels, and file integrity *at end of session*, before the room is torn down. Silent capture failures discovered a week later are the single most common way corpora die.

---

## 4. Phase 2 — Annotation schema (the actual scarce asset)

This is the phase that separates the project from an ambient-scribe archive. **The recording is not the dataset. The recording plus your read of it is the dataset.**

Three problems the raw tape cannot solve:

**4.1 Signal-to-noise.** The performance-art moments — de-escalation, breaking bad news, converting a refusal into a yes — are on the order of **3–5% of runtime**. Train on raw AVI and the model learns the *mean* of your practice, which is unremarkable by construction. The hard part of the harness is **selection**, not ingestion.

**4.2 Style is a function of the read, not a constant.** What makes the delivery work is not warmth — it is *selected* warmth: brisk with one patient, slow with the next, and the selection is driven by what you are seeing. An adapter trained as "sound like the clinician" flattens this into a single register applied uniformly. That is a caricature, and it is dangerous (§7.2). To reproduce the modulation, training segments must be **paired with the clinical/interpersonal state you were reading at the time.**

**4.3 Efficacy labels are out of frame.** Some consequences are in-frame (shoulders drop, tone shifts, interruptions stop) and those are learnable. Adherence, return visits, whether the prescription was filled — out of frame, and no architecture recovers them from pixels. Without outcome linkage, every encounter carries equal weight, including the ones where you were wrong.

### Proposed record schema

```jsonc
{
  "session_id": "uuid",
  "clip": { "t_start": 812.4, "t_end": 899.1 },   // session clock
  "segment_class": "de-escalation | bad-news | refusal-conversion |
                    reassurance | instruction-delivery | routine",
  "read": {                                        // clinician state at t_start
    "acuity": "low | moderate | high",
    "affect_observed": "angry | frightened | dismissive | flat | ...",
    "prior_relationship": "none | established",
    "register_selected": "brisk | slow | warm | firm | ...",
    "why": "free text — what in the presentation drove the register choice"
  },
  "move": {
    "description": "free text — what you did and what you were going for",
    "discriminating_feature": "the thing that made this call rather than another"
  },
  "in_frame_response": "free text — observable shift, if any",
  "outcome": {                                     // added later, separate pass
    "plan_accepted": true,
    "adherence_known": "filled | not-filled | unknown",
    "return_72h": false,
    "source": "chart | callback | unknown"
  },
  "self_rating": { "worked": 1, "confidence": 0.7 }  // include failures
}
```

**Non-negotiable:** annotate failures. A corpus of only your wins cannot teach the model what distinguishes an effective move from a habit, because there is no contrast class. This is the same defect as medfit's — everything in it read as competent.

**Automation reality check.** The selection and `read` fields are your judgment and resist automation — which cuts directly against the scale ambition. Realistic path: automate segmentation candidates (VAD, speaker turns, prosodic change-point detection, affect classifiers) to produce a *review queue*, and keep the human in the labeling seat. Target throughput ~2 min annotation per selected clip. That is the true rate limit on corpus growth; model it explicitly.

---

## 5. Phase 3 — Harness software

Modules, in dependency order. Python; this is squarely in the existing stack (FastAPI, GCP, Docker, local MLX).

1. **Ingest** — watch folder → integrity check → sync verify → canonical session store. Content-addressed. Immutable originals.
2. **Derive** — transcription with speaker labels (whisper-class, diarized), prosodic feature extraction (F0 contour, energy, speech rate, pause distribution), pose/gaze track. All derived artifacts regenerable; never overwrite originals.
3. **Candidate detection** — heuristics + classifiers proposing clip boundaries by class. Tunable precision/recall; bias to recall, the human filters.
4. **Annotation UI** — clip player (multi-angle, waveform, transcript), schema form, keyboard-driven. This is the tool you will spend the most hours in; build it well or the corpus will not get made.
5. **Outcome linkage** — separate deferred pass joining chart/callback data by session_id. Must survive the annotator not knowing outcomes at label time.
6. **Export** — architecture-neutral corpus manifest: clip refs + aligned modalities + annotations. Emit adapter-format views (text-pair, audio-text, AV-latent) as *downstream transforms*, never as the storage format.
7. **Provenance** — every derived artifact records tool version and parameters. Five-year horizon means you will regenerate derivations several times.

**Design rule:** originals immutable, everything else regenerable, export formats disposable. You do not know what the 2031 encoder wants.

---

## 6. Phase 4 — Evaluation (design this before training anything)

The session's central lesson: **the evaluation is where these projects fail, not the training.** medfit's authors ran an eval that measured style and reported it as medical improvement, and it passed manual review.

Precedent to reuse: the cross-model-persona-steering validation tests that distinguished true steering from temperature artifacts. Same discipline, new domain.

**Must-have tests:**

- **Base vs. adapter, same prompt, blind.** Always. This is the only test that ran cleanly this session and it is the floor, not the ceiling.
- **Confound control.** The Qwen3 comparison was muddied by `<think>` block behavior changing under the adapter. Identify the equivalent confound for whatever base you use, and neutralize it before reading results.
- **Modulation test (the important one).** Present matched clinical scenarios that *should* elicit different registers. Does the adapter modulate, or does it apply one register uniformly? Uniform warmth = caricature = failure, regardless of how good the single register sounds.
- **Assertion-strength leakage.** Take a set of high-urgency instructions with known-correct content. Does the adapter soften them? This is the medfit mechanism (confident assertion learned without content) and it is the highest-severity failure mode in deployment.
- **Fabrication probe.** medfit's adapter invented a statistic. Test explicitly for numbers, thresholds, and specifics appearing in adapted output that were absent from base output.
- **Blinded human rating.** Patients or naive raters, adapter vs. base vs. you. Rate on warmth, clarity, trust, and *whether they would follow the plan*. Do not rate on "sounds like the author" — that is agreement-with-author, which is indistinguishable from correctness to a loss function.

**Falsification question, stated up front:** *what result would convince me the adapter reproduced my style's appearance without its efficacy?* If there is no such result, the eval is decorative.

---

## 7. Phase 5 — Deployment tiers (ordered by defensibility)

**Tier 1 — Disclosed callbacks on closed cases. Defensible; build first.**
You saw the patient, you made the call, the AI delivers *your* decision in a register that helps it land. Requirements: AI disclosure up front, scope limited to results and instructions already decided, hard escalation path the moment the patient introduces anything unexpected. This is a real product and it is buildable well before the target architecture exists.

**Tier 2 — Draft-and-approve.** Model composes in your style; you approve; it delivers. Preserves judgment as yours, scales delivery. Natural extension of Tier 1.

**Tier 3 — Coaching / education.** Model watches a resident encounter and flags what you would have done differently. Real value, real market, sits in education rather than clinical care, and the liability profile is completely different. Arguably the best near-term commercial target and it uses the same corpus.

**Tier 4 — Pre-visit avatar reassurance. Highest risk; do not lead with this.**
The specific problem: your reassurance is *calibrated*. It carries information because you looked at the patient and formed a judgment, and the patient — who cannot read your differential — reads your affect as evidence. Reassurance delivered *before* assessment emits the signal of safety decoupled from the fact of it. The failure is asymmetric: the patient who is calmed and therefore does not come in is the one you never hear about. An honest self-estimate puts reassurance at ~95% performance; the other 5% is where people die and they do not announce themselves at the door. If Tier 4 ships, it ships with a triage gate the model does not control.

---

## 8. Known biases and limits in this plan

Requested explicitly, and worth keeping in the document.

**8.1 Selection effect in the founding observation.** The patients who bounce from scripted, paternalistic providers land in your exam room and complain. The ones who received identical care and were satisfied never appear in your sample. The observation that delivery is the limiting factor is drawn from a sample of dissatisfaction and will systematically overstate the effect size.

**8.2 The defensive-delivery equilibrium is institutional, not personal.** Scripted hedging is partly a medicolegal artifact — clinicians learn that warmth and confidence create exposure. An adapter that delivers protocol with your confidence, deployed in a system that punishes confidence, has an institutional problem before it has a technical one.

**8.3 "The plan is protocol anyway" holds until it doesn't.** The ability to make a plan land is exactly as strong when the plan is wrong. Delivery quality amplifies whatever it is attached to.

**8.4 Agreement-with-author is indistinguishable from correctness.** You will be authoring the annotations, selecting the clips, and rating the outputs. Every one of those is a channel for encoding your priors as ground truth — which is precisely the defect you diagnosed in medfit, with better clinical judgment behind it. The failure mode does not require bad intent; it requires only that confidence exceed evidence.

**8.5 Undocumented expertise vs. preferred views.** The strong version of this project captures tacit skill that is real, effective, and absent from text corpora because nobody writes it down. The weak version encodes your preferences on contested questions and calls it expertise. From the inside these are not always distinguishable. Build the outcome-linkage pass (§4.3) specifically because it is the only thing that separates them.

**8.6 Architecture risk.** The entire plan is a bet that any-to-any latent-predictive models arrive at local scale in ~5 years and accept adapter-style conditioning. If they arrive but condition differently, the corpus survives (it is architecture-neutral by design) and the adapter methodology does not. Acceptable risk; the corpus is the asset.

**8.7 Throughput risk — most likely cause of death.** Annotation is human-rate-limited (§4). If the sustainable rate is 10 annotated clips/week, five years yields ~2,500. That may be sufficient for adaptation on top of a strong pretrained interaction model, and is certainly insufficient for anything trained from scratch. Model the corpus growth curve before Phase 1 and decide whether the number at the end is worth the build.

---

## 9. Immediate next actions

*Item numbering is preserved from v0.1 (ADC entries cite §9.4); status as of 2026-07-28 is annotated in place.*

1. Healthcare counsel: ownership of encounter recordings under the applicable practice arrangement (§0.1). **Still blocking** — but under the institutional path (§0.6) this is answered by the partner's arrangements rather than negotiated solo.
2. Malpractice carrier: position on retained encounter video (§0.3). **Still blocking**; likewise shifts to institutional counsel under §0.6.
3. IRB pre-submission conversation — scope must cover training and deployment, not just collection. **Still open**, and now the partner institution's IRB rather than one sought independently.
4. Draft the annotation schema against **10 encounters from memory**, no recording. If the schema cannot capture what mattered in encounters you remember well, it will not capture it on tape. Cheapest possible test of the core premise. **Instrument built** (`tools/memory_annotation.html`); the 10-encounter pass itself is **still outstanding** and remains the only privileged-read test of the schema — the author is the sole person who can run it.
5. Throughput model: annotated clips/week × 260 weeks. Decide if the terminal number justifies the build. **Done** (ADC-003): supply-bound, not annotation-bound; consent rate and shifts recorded dominate corpus size, annotation-UI throughput does not.
6. Only then: capture rig spec and hardware. **Unchanged and still gated** by 1 and 2.

**Added since v0.1, not in the original list:** the conditioning pathway was tested end to end on archival film while capture stayed gated (ADC-012..015). It produced a measured null on register conditioning and fixed a patient-voice defect traceable to single-track audio. Both results feed §0.6's protocol requirements.

---

## 10. Questions to put to the Claude Code session

- Annotation UI architecture — local Electron/Tauri vs. web + local server. Latency on multi-angle scrub is the deciding factor.
- Storage tiering: immutable originals on cold object storage, derived artifacts local, annotation DB local. GCS + local NVMe, or fully local given PHI?
- Candidate-detection pipeline: which prosodic change-point and affect models run acceptably on 64 GB Apple silicon, and what is the precision/recall tradeoff at the review-queue stage?
- Corpus manifest format that survives five years of unknown downstream encoders — WebDataset, Arrow, or custom.
- Whether Tier 3 (coaching) can be built against a much smaller corpus and shipped first to fund the rest.

---

*Prepared 2026-07-22. Findings in §1 are reproducible with grep against `mlx-community/medfit-dataset`. Legal content is not legal advice.*
