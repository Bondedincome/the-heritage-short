# Experiments

Format: one entry per experiment. Evidence = `runs.jsonl` run_id / input_key, file names in `outputs/`, numbers.
Target: about ten entries. Old outputs are never overwritten (the content hash is in each filename), so A and B stay comparable.

Ideas this pipeline makes cheap to test:
- FLUX.1 schnell (text only) vs FLUX.2 with the reference sheet as an input image: does character consistency improve?
- Fixed seed per shot vs none; steps 4 vs 8
- Square master + crop vs generating each aspect ratio separately
- Wan 2.2 draft (--draft) vs full settings: latency vs quality on a T4; fp16 vs bf16
- Gemini TTS voices (Kore / Charon / ...) and delivery direction text for your language
- Ducking ratio / music volume_db; three ACE-Step seeds
- Ken Burns vs Wan for the same shot, shown blind to your audience

---

## E01 - <title>
- **Hypothesis:**
- **Change:** (storyboard field / flag, from → to)
- **Result:** (numbers + what you saw/heard; evidence: run_id, files)
- **Decision:**
