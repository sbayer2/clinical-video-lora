# Research: Tier-3 coaching feasibility (plan section 10, item 5)

Agent-researched 2026-07-22. Sources cited inline.

# Can a Clinical-Communication Coaching Product Ship on a Small Expert-Annotated Corpus?

## 1. Sample efficiency: is 500–2,500 richly-annotated clips enough?

**Short answer: yes, plausibly — provided the corpus is used to condition/adapt a strong base model, not to train from scratch. The literature converges on ~1k high-quality examples as sufficient for style/judgment alignment, and the strongest existing comps in this exact domain trained supervised models on 341–1,553 sessions.**

Key evidence:

- **LIMA (Zhou et al., 2023)**: LLaMA-65B fine-tuned on only 1,000 carefully curated prompt-response pairs achieved strong alignment, with the "superficial alignment hypothesis" — nearly all capability comes from pretraining; small high-quality data teaches format, style, and judgment. Learned response formats "from only a handful of examples." ([arXiv:2305.11206](https://arxiv.org/abs/2305.11206))
- **LIMIT (Databricks, 2023)**: confirms a few thousand samples suffice, with the critical caveat that the fine-tuning set must match the evaluation paradigm you care about — directly relevant here: annotations must look like the coaching outputs the product will emit (clinical read + register choice + rationale, which is exactly what is being annotated). ([Databricks LIMIT](https://www.databricks.com/blog/limit-less-more-instruction-tuning))
- **LoRA practice literature (2024–2025)**: "a few hundred high-quality examples" is the working norm for single-domain style/judgment adapters; quality dominates quantity, and LoRA is sensitive to noisy/inconsistent annotation — an argument *for* a single-annotator, rationale-rich corpus (high internal consistency) over a large crowd-coded one. Caveat: one empirical study finds LoRA is a "slow learner" with training instability at very low data/task counts, so full fine-tuning of a small model or ICL may beat LoRA at the ~500-clip end. ([Raschka, practical LoRA tips](https://magazine.sebastianraschka.com/p/practical-tips-for-finetuning-llms); [PEFT empirical study](https://arxiv.org/pdf/2411.16775))
- **Many-shot in-context learning (Agarwal et al., NeurIPS 2024 Spotlight)**: with long-context models, conditioning on hundreds-to-thousands of in-context exemplars produces gains that rival or obviate fine-tuning. The corpus is useful from day one via retrieval of the most-similar exemplar clips into the prompt — no training run required at 500 clips, adapter training becoming attractive around 1,500–2,500. ([arXiv:2404.11018](https://arxiv.org/abs/2404.11018))
- **Domain-specific existence proofs at this scale**: Tanana/Atkins/Imel predicted MI skill codes at near-human reliability from **341 transcripts** (pre-LLM, 2016) ([PMC4842096](https://pmc.ncbi.nlm.nih.gov/articles/PMC4842096/)); Flemotomos et al. scored CBT sessions on the 11-code CTRS from **1,118 sessions** at F1 ≈ 0.73 with BERT ([PLOS One](https://journals.plos.org/plosone/article?id=10.1371%2Fjournal.pone.0258639)); Althoff/Sharma's EPITOME empathy model used **10k annotated (post, response) pairs** but at the *utterance* level — a session/clip-level analogue is far smaller ([ACL 2020](https://aclanthology.org/2020.emnlp-main.425/)). These all predate strong base models.
- **One important negative result**: zero-shot GPT-4o on fine-grained MI strategy labeling is mediocre (<60% accuracy; poor recall on low-quality sessions under class imbalance) ([BMC Psychiatry 2025](https://link.springer.com/article/10.1186/s12888-025-07391-1)). Implication: naive prompting is not enough — the expert exemplar corpus is precisely the ingredient that fixes this, and it also means the corpus doubles as the indispensable *evaluation set* proving the product beats generic ChatGPT.

**Assessment**: 500 clips supports a retrieval/many-shot ICL product plus a credible held-out eval; 1,500–2,500 supports a LoRA/adapter pass. Richness per clip (read + register + rationale) is worth a multiple of bare labels — this matches LIMA's curation logic. Main risks: single-annotator idiosyncrasy (no inter-rater check; frame the product explicitly as "coach in the style of Dr. X" or add a second rater on a subsample) and distribution coverage (urgent care case-mix diversity across 500 clips).

## 2. Lyssn.io — the key commercial comp

**What they score**: 50+ metrics from session recordings/transcripts — MI fidelity metrics derived from MISC 2.1/2.5 and MITI 4.2.1 (reflections, questions, empathy, collaboration global ratings; utterance-level behavior codes; session-level summary scores), plus CBT (CTRS-based), general skills, speaker diarization and role ID. Target ~80% agreement with human raters; per-metric performance published. ([Lyssn Clinical Quality Metrics PDF](https://www.lyssn.io/wp-content/uploads/2022/06/Lyssns-Clinical-Quality-Metrics-1.pdf); [Predicting MI fidelity](https://www.lyssn.io/resources/insights/predicting-mi-fidelity-like-a-human/))

**Corpus scale**: not published directly, but the founders' (Atkins, Imel, Tanana) academic lineage is on record: **341 MI transcripts** for the original code-prediction models, ~**1,553 sessions** for topic-model work, ~**1,118 sessions** for CBT/CTRS scoring. Order of magnitude: **hundreds to low thousands of expert-coded sessions** — squarely the scale of the proposed corpus.

**Regulatory posture**: no FDA clearance found and none claimed; positioned as HIPAA-compliant training/QI/supervision software for provider organizations — workforce quality improvement, not patient-facing clinical care. This is the posture to copy.

**Traction**: NIH-seeded, ~$2M reported private raise ([Tiny Tech Fund](https://www.tinytechfund.com/wa/lyssnio-inc)); customers include child-welfare agencies, CCBHCs, 988/crisis lines, Centerstone, Ontrak Health. A decade of academic groundwork, modest capital — a viable niche business, not a rocket. Adjacent competitor Eleos raised $60M for behavioral-health AI ([MobiHealthNews](https://www.mobihealthnews.com/news/eleos-secures-60m-behavioral-health-ai-agents)).

**Medical (non-psychotherapy) equivalents — research-stage, not productized**:
- **ConverSense** (UW, CHI 2024): automated social-signal feedback (dominance, warmth, engagement, interactivity) from primary-care visit audio, clinician-facing feedback UI; explicitly motivated by RIAS being too labor-intensive. ([CHI paper](https://dl.acm.org/doi/10.1145/3613904.3641998); [JAMIA Open usability study](https://academic.oup.com/jamiaopen/article/7/4/ooae106/7826763))
- **CommSense** (UVA): smartwatch-captured real encounters, NLP + LLM feedback on empathy/clarity/presence in palliative care — only **51 encounters, 7 clinicians** as of Aug 2025. ([PMC10338668](https://pmc.ncbi.nlm.nih.gov/articles/PMC10338668/); [arXiv:2407.08143](https://arxiv.org/abs/2407.08143))
- **RIAS automation**: ML topic-annotation consistent with RIAS ([PMC3991772](https://pmc.ncbi.nlm.nih.gov/articles/PMC3991772/)); social-signal/bias detection ([arXiv:2407.17477](https://arxiv.org/pdf/2407.17477)).
- **Althoff/Sharma EPITOME**: empathy classification + rationale extraction; found peer supporters do not self-improve without feedback — the core argument for a feedback product. ([arXiv:2009.08441](https://arxiv.org/abs/2009.08441))

**No commercial Lyssn-for-medical-encounters was found. That is the gap this product would occupy.**

## 3. AI standardized-patient / communication-training products (2026)

| Product | What it does | Feedback on *real* encounters? |
|---|---|---|
| Kognito | Scripted virtual-human role-play | No — simulation only |
| SimConverse | Generative-AI patients, 1,000+ characters, 50+ institutions; de-escalation, bad news | No — simulation only |
| PCS Spark | Speech-driven AI patient simulator, automated evaluation vs generic frameworks | No — simulation only |
| Oscer AI | Virtual cases + feedback for OSCE prep | No — simulation only |
| SOPHIE (U. Rochester) | LLM avatar + feedback for serious-illness conversations; RCT (n=30) showed gains | No — simulation only |
| MedSimAI (academic) | LLM simulation + formative feedback | No |

**Ambient scribes (Abridge, Nuance DAX, Suki, Ambience, Nabla, Freed)**: documentation, coding, prior-auth, order staging. **No shipped communication-quality/coaching feature found at any of them** as of mid-2026; category expansion is toward revenue cycle, not coaching. Strategic implication both ways: the lane is open, but scribes own the audio pipe and could bolt this on — speed matters, and the defensible asset is the expert annotation layer, not transcription.

## 4. Validated coding schemes to align with

- **RIAS (Roter)** — most-used med-encounter system; good as a *construct map*, not the product's output format.
- **Calgary-Cambridge Guide + OS-12** — dominant teaching framework; validated rating instrument with acceptable reliability on audio — **best fit for structuring urgent-care coaching output** (complete, compressed consultations). ([OS-12 codebook, PMC7201796](https://www.ncbi.nlm.nih.gov/pmc/articles/PMC7201796/))
- **SEGUE** — checklist-style, widely used in US med-ed; little automation literature.
- **MITI/MISC** — best-automated scheme (Lyssn's backbone) but wrong construct for most urgent-care visits; borrow its *global ratings + behavior counts + flagged moments* report structure.
- **VR-CoDES** — patient emotional cues/concerns and provider responses; validated, early ML work (86 consultations), maps directly onto the "register selection" annotations — **strong fit for the empathic-responsiveness slice**. ([PubMed 20430562](https://pubmed.ncbi.nlm.nih.gov/20430562/))

**Recommendation**: report structure = Calgary-Cambridge/OS-12 process phases × VR-CoDES-style cue-response analysis, presented MITI-style (global score + counted behaviors + flagged moments). Cross-walking bespoke annotations to these schemes converts a personal corpus into a publishable, credible instrument.

## 5. Regulatory / liability

**A clinician-facing, education/coaching-only tool operating on past encounters is, on current FDA policy, not a medical device.** Three independent bases:

1. **Explicit FDA non-device example**: "Software functions intended for health care professionals to use as educational tools for medical training or to reinforce training previously received are not devices." ([FDA: software functions that are NOT medical devices](https://www.fda.gov/medical-devices/device-software-functions-including-mobile-medical-applications/examples-software-functions-are-not-medical-devices))
2. **Intended use**: retrospective analysis of completed encounters for skill development, not care of the recorded patient. Marketing/labeling must stay strictly on the education side (no "flags missed diagnoses," no real-time in-encounter prompts — those cross into CDS/SaMD).
3. **Even if construed as CDS**, the revised January 2026 CDS guidance is more permissive, though silent on generative AI and coaching specifically — stand on the education carve-out. ([Covington summary](https://www.cov.com/news-and-insights/insights/2026/01/5-key-takeaways-from-fdas-revised-clinical-decision-support-cds-software-guidance); [FDA CDS FAQs](https://www.fda.gov/medical-devices/software-medical-device-samd/clinical-decision-support-software-frequently-asked-questions-faqs))

Precedent: Lyssn operates at scale in this posture with no FDA clearance. Residual obligations: HIPAA (recordings are PHI; BAAs), state two-party consent for recording, peer-review/QI privilege questions if feedback records become discoverable — worth structuring under institutional QI programs.

**Boundary warnings**: (a) analyzing the *audio signal* itself (prosody/acoustics) edges toward "signal acquisition system" territory excluded from non-device CDS — text-transcript analysis is the safer substrate; (b) any drift toward patient-facing feedback or real-time care guidance changes the analysis entirely.

## Bottom line

**Can Tier 3 ship first on a small corpus? Yes — with the right architecture.** (i) LIMA/LIMIT/many-shot-ICL establish ~1k rich exemplars suffice on a strong base model; at 500 clips retrieval-conditioned ICL works with no training at all. (ii) Lyssn built a real business on models trained from ~341–1,553 expert-coded sessions — the proposed 500–2,500 clips is the historically demonstrated scale, now with far stronger base models. (iii) Zero-shot LLMs underperform on fine-grained communication coding — the expert corpus is the moat. (iv) The regulatory path (clinician education, retrospective, transcript-based) is clean and precedented.

**Strongest comp: Lyssn.io.** No one has shipped its equivalent for general medical encounters; nearest efforts are academic feasibility studies with ≤51 real encounters, and simulation vendors coach against fake patients. Two structural risks: ambient-scribe vendors own the audio capture layer and could enter quickly; a single-annotator corpus needs an inter-rater or external-validity check (cross-walk to OS-12/VR-CoDES on a subsample) before its judgments can be marketed as more than one expert's style.
