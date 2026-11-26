# Failures

What broke, what I tried, what fixed it (or how I worked around it). `runs.jsonl` lines with
`"status": "error"` or `"retry"` are the raw evidence: cite run_id / ts.

Categories to watch: quota (Gemini RPM/daily, Cloudflare neurons, Kaggle GPU hours), memory (Wan on a
16 GB T4), consistency (same character across shots), language (TTS pronunciation of your language,
translation quality), tool drift (model ids or APIs that moved since the brief was written).

---

## F01 - <title>
- **When / where:** (stage, date, run_id)
- **Symptom:** (exact error text)
- **Tried:**
- **Fix / workaround:**
- **What I'd do differently:**

## F01 - TLS: CERTIFICATE_VERIFY_FAILED on every provider
- **When:** first `python run.py smoke`, 26 Nov, both Gemini and Cloudflare.
- **Symptom:** `SSLCertVerificationError ... unable to get local issuer certificate`.
- **Cause:** Python's certifi bundle doesn't trust the chain this Windows machine sees (HTTPS inspection by local software).
- **Fix:** `truststore.inject_into_ssl()` in `pipeline/providers.py`, which verifies against the Windows cert store. Verification is still on, not disabled.

## F02 - Gemini 3.8 Flash (text): 503 "high demand" and 300 s read timeouts
- **Symptom:** smoke test: 503 after a 193 s hang; `script translate`: a read timeout at 300 s per attempt (see runs.jsonl, stage=script). The translation took 654 s.
- **Fix:** `script.text_call` falls back from newest flash to newest flash-lite on 429/5xx/timeouts.
- **Still to do:** a shorter HTTP timeout for text calls; 300 s is too generous.

## F03 - `auto` model resolution picked flash-lite-tts over flash-tts
- **Cause:** the sort key tied on version (3.8) and preview status; list order decided.
- **Fix:** `lite` now ranks below non-lite in `providers._version`.

## F04 - Cloudflare FLUX.2 klein safety filter (AiError 3030 "output has been flagged")
- **Hit:** the refsheet for `mehari`, keyframes s01 (boy "roaring like a lion" with uncle pointing) and s06 (grandfather "frowning... determined").
- **Fix:** softened the wording ("pretending to be a lion", "stern, comic expression"); s01/s06 then passed on the first try.
  The mehari refsheet still failed, so shots with him have no reference image (consistency risk).

## F05 - Gemini TTS read the delivery direction aloud
- **Symptom:** every line came out 15-20 s, even 20-character lines: the model spoke "Read this in Amharic like a young man..." as part of the line.
- **Fix:** emptied `voice.direction`; lines are now 3-8 s. **Lesson:** with this TTS, style text in the same text part is not reliably treated as an instruction.

## F06 - Daily TTS quota exhausted on gemini-3.8-flash-tts
- **Symptom:** HTTP 429 with a per-day quota after ~14 calls (smoke + the first full narration pass + 2 more).
- **Fix:** the pipeline detected "daily" and stopped retrying; switched `voice.model` to `gemini-3.8-flash-lite-tts` (separate quota).
  Its Amharic quality has not been compared yet.

## F07 - Narration longer than shots
- **Fix:** shot durations were re-fitted to the measured WAV length + offset + 0.7 s (film now 80 s incl. end card).

## F08 - Time budget: Wan 2.2 and ACE-Step skipped for the first submission
- The work started late on deadline day; the shots use the Ken Burns fallback and the score is the synthesised placeholder pad.
