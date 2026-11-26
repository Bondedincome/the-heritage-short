# Ethics: consent, licences, labelling

## Consent
One entry per real person whose story, face, likeness or voice appears. **A real person shown or voiced
without an entry here scores zero.** In `storyboard.json`, a character with `"real_person": true`
must have `consent_ref` pointing to an entry here; `python run.py validate` enforces it.

How likeness is handled: I shared one photo of each person with my coding agent only so it could write a
short **text** description (hair, clothing, age). **No photo was uploaded to an image or video model.** Characters
are generated as storybook illustrations from those text descriptions, so they are evocative, not photo-real
likenesses. **No real voice is imitated:** narration uses a stock Gemini TTS voice (`voice.voice_name` in
`storyboard.json`), and Babeye's line is quoted by the narrator, not spoken "as" him. The photos are not in this repository.

| Consent id | Person (relation) | Story told | Illustrated likeness | Voice imitated | How / when consent was given | Heard the narration words |
|---|---|---|---|---|---|---|
| consent-doni | Doni (me, the narrator) | yes | yes | no | I am the author | yes |
| consent-babeye | Babeye (grandfather) | yes | yes | no | Agreed when I asked for his photo for this film (date: ____, via ____) | ____ (send radio cut) |
| consent-hule | Hule (mother) | yes | yes | no | Agreed when I asked for her photo for this film (date: ____, via ____) | ____ (send radio cut) |
| consent-mehari | Mehari (uncle) | yes | yes | no | Agreed when I asked for his photo for this film (date: ____, via ____) | ____ (send radio cut) |

Each person can withdraw by telling me; the film would then be taken down and their character removed from
`storyboard.json`. The radio cut is sent to Hule and Mehari (and read/played to Babeye) so they hear the exact
words before release; that doubles as part of user test 1.

## Private data
Free endpoints may use inputs for training. Sent: first names/nicknames, the anecdote, and generic visual
descriptions. Not sent: photos, surnames, addresses, ages/dates of birth, anything about health.

## Licences
| Component | Model / tool | Licence / terms | Output usage |
|---|---|---|---|
| Keyframes | FLUX.1 [schnell] via Cloudflare Workers AI | Apache 2.0 (verify) + Cloudflare terms | |
| Motion | Wan 2.2 TI2V-5B | Apache 2.0 | |
| Music | ACE-Step 1.5 | (verify) - outputs usable commercially per brief | |
| Narration | Gemini TTS | Gemini API terms | |
| Script/translation | Gemini | Gemini API terms | |
| Edit | FFmpeg (imageio-ffmpeg build) | LGPL/GPL | |
| Fonts | | | |

## Labelling
- Visible end card: "Made with AI" + tools (from `storyboard.json` end_card)
- MP4 metadata comment names the tools
- Platform AI-content disclosure switched on when posting: platform ____, screenshot ____
- Watermarks / provenance metadata (SynthID etc.) not stripped
- Style prompts describe qualities, not living artists
