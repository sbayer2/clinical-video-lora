# Data and Annotation License — All Rights Reserved

Copyright (c) 2026 Steven Bayer. All rights reserved.

**This file governs data. The code is separate.** Source code, schema
definitions, documentation, and configuration in this repository are licensed
under the Apache License, Version 2.0 (see [`LICENSE`](LICENSE)). Nothing in
that license grants any right in the material described below. Where the two
could be read to overlap, this file controls for data.

## Scope

"Data" here means, whether now or in future, and whether or not it ever
appears in this repository:

- encounter video and audio recordings, in any format or resolution;
- transcripts, diarization, alignments, and any other derived representation
  of recorded speech;
- **annotations and labels** — clinical reads, selected registers,
  communicative moves, observed responses, self-ratings, and every other
  field of the encounter record schema as populated with real content;
- corpora, exports, training pairs, and evaluation sets built from any of
  the above;
- model weights, adapters, or checkpoints trained or fine-tuned on any of
  the above.

## No data is in this repository

By design. Recordings, annotation databases, exports, training pairs, and
adapter weights are excluded by `.gitignore` and have never been committed.
A clone of this repository contains the instrument, not the corpus — see the
README for what can and cannot be reproduced from it.

Two tracked files are **not** data under this definition and are covered by
the Apache-2.0 license with the rest of the code:
`schema/encounter_record.schema.json` (the empty schema — field definitions,
no content) and `eval/ood_scenarios.json` (synthetic evaluation scenarios,
authored, not observed).

## Reservation

All rights in the Data are reserved. Without prior written permission of the
copyright holder, and independent of any right granted by the Apache-2.0
license over the code, you may not:

1. access, copy, reproduce, or redistribute the Data;
2. create derivative works from the Data;
3. **use the Data to train, fine-tune, or evaluate machine-learning models**;
4. sublicense, sell, rent, or lease the Data.

## Governance of any future corpus

Any encounter data collected under a research partnership will be governed by
the applicable institutional review board protocol and the written agreement
between the parties — not by this file and not by the code license. Where a
partnering institution holds rights in a corpus and its annotations, that
allocation controls. This file reserves the author's position in the absence
of such an agreement; it does not purport to describe one.

Patient authorization, data ownership, and rights in a recording are separate
questions that consent alone does not resolve. See `docs/harness-action-plan.md`
§0 for how those predicates are being worked.

---

Nothing in this repository is medical or legal advice.

For licensing and data-access inquiries: sbayer2@gmail.com
