# GPU stages on Kaggle

Wan 2.2 (moving shots) and ACE-Step 1.5 (score) need a GPU. They run in a Kaggle notebook. The local
pipeline only hands jobs over and takes results back, keyed by content hash, so a clip made for an
old prompt or an old keyframe is never used by mistake.

## One-time setup
1. Phone-verify your Kaggle account (needed for GPU and internet in notebooks).
2. Create a dataset named `heritage-code` and upload `kaggle/wan22_i2v.py` and `kaggle/ace_step_music.py`.

## Moving shots (Wan 2.2 TI2V-5B)
```bash
python run.py keyframes              # keyframes must exist first
python run.py shots --bundle         # -> outputs/kaggle/shots_bundle.zip (only missing/changed shots)
```
1. Upload `shots_bundle.zip` as a new Kaggle dataset, or as a new version of the existing one.
2. New notebook → Settings: **Accelerator: GPU T4 x2**, **Internet: on**. Add both datasets as inputs.
3. Run:
   ```
   !pip install -q -U "diffusers>=0.35" transformers accelerate ftfy imageio imageio-ffmpeg
   !python /kaggle/input/heritage-code/wan22_i2v.py --draft     # quick check on 1-2 shots: add --only s01
   !python /kaggle/input/heritage-code/wan22_i2v.py
   ```
4. Download `/kaggle/working/shots_out/` (Output tab), then run:
   ```bash
   python run.py shots --import ~/Downloads/shots_out
   python run.py assemble
   ```

Tip: `--draft` is for judging motion only. A draft clip is imported like any other clip, so run the full
settings before your final edit, or delete the draft clip from `outputs/shots/wan/`.

## Score (ACE-Step 1.5)
```bash
python run.py music --bundle         # -> outputs/kaggle/music_bundle/music_jobs.json
```
Upload it, run `ace_step_music.py` the same way, download `music_out/`, then run:
```bash
python run.py music --import ~/Downloads/music_out
python run.py music                  # lists m1..m3; listen and choose
```
Set `music.chosen` and `music.why_chosen` in `storyboard.json`.

## Optional: the Kaggle CLI
`pip install kaggle`, put your token in `~/.kaggle/kaggle.json`, then:
```bash
kaggle datasets version -p outputs/kaggle/shots_bundle -m "shots v2"
kaggle kernels output <user>/<notebook> -p ./kaggle_returns
```
