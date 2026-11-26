"""ACE-Step 1.5 score candidates, run inside a Kaggle or Colab GPU notebook.

Inputs : music_jobs.json from outputs/kaggle/music_bundle/ (upload as a dataset, or copy into Colab)
Outputs: /kaggle/working/music_out/m1-<key>.wav ... and runs_kaggle.jsonl
Then locally:  python run.py music --import path/to/music_out
               set music.chosen (and music.why_chosen) in storyboard.json

Setup (check the ACE-Step 1.5 README for the current install line; it changes):
    !git clone https://github.com/ace-step/ACE-Step-1.5 && pip install -q -e ACE-Step-1.5

The generate() adapter below is the only part tied to ACE-Step's Python API. If the API
has moved, fix generate() and record the change in FAILURES.md; the job/output contract stays.
"""
import argparse
import glob
import json
import os
import shutil
import socket
import time
import uuid

ap = argparse.ArgumentParser()
ap.add_argument("--jobs", default=None)
ap.add_argument("--out", default="/kaggle/working/music_out")
ap.add_argument("--steps", type=int, default=None, help="override inference steps")
args = ap.parse_args()

jobs_path = args.jobs or sorted(glob.glob("/kaggle/input/**/music_jobs.json", recursive=True)
                                + glob.glob("/content/**/music_jobs.json", recursive=True))[0]
jobs = json.load(open(jobs_path))["jobs"]
os.makedirs(args.out, exist_ok=True)
log_path = os.path.join(args.out, "runs_kaggle.jsonl")

_pipe = None


def generate(job: dict, dest: str) -> None:
    """Adapter: try the known ACE-Step Python entry points, newest first."""
    global _pipe
    tags = job["prompt"]
    lyrics = job["lyrics"]
    # --- ACE-Step pipeline-style API -----------------------------------------------------
    try:
        from acestep.pipeline_ace_step import ACEStepPipeline  # v1-style API
        if _pipe is None:
            _pipe = ACEStepPipeline(dtype="float16", cpu_offload=True)
        kw = dict(audio_duration=float(job["duration"]), prompt=tags, lyrics=lyrics,
                  manual_seeds=[int(job["seed"])], save_path=dest)
        if args.steps:
            kw["infer_step"] = args.steps
        _pipe(**kw)
        return
    except ImportError:
        pass
    raise RuntimeError("No known ACE-Step Python API found. Open the ACE-Step 1.5 README, update generate(), "
                       "and log the change in FAILURES.md.")


for job in jobs:
    dest = os.path.join(args.out, job["output"])
    if os.path.exists(dest):
        continue
    t0 = time.time()
    rec = {"call_id": uuid.uuid4().hex, "stage": "music", "shot_id": job["id"], "provider": "kaggle",
           "model": job["model"], "seed": job["seed"], "input_key": job["key"],
           "params": {"prompt": job["prompt"], "lyrics_chars": len(job["lyrics"]), "duration": job["duration"],
                      "steps": args.steps}, "host": socket.gethostname()}
    try:
        import torch
        rec["gpu"] = torch.cuda.get_device_name(0) if torch.cuda.is_available() else "cpu"
    except Exception:
        pass
    try:
        generate(job, dest)
        if not os.path.exists(dest):  # some versions append their own suffix
            made = sorted(glob.glob(os.path.splitext(dest)[0] + "*"), key=os.path.getmtime)
            if made:
                shutil.move(made[-1], dest)
        lat = round(time.time() - t0, 1)
        rec.update(status="ok", latency_s=lat, gpu_seconds=lat, cost_usd=0.0, output=job["output"])
        print(f"{job['id']}: {lat}s")
    except Exception as e:
        lat = round(time.time() - t0, 1)
        rec.update(status="error", latency_s=lat, gpu_seconds=lat, cost_usd=0.0, error=str(e)[:600])
        print(f"{job['id']} FAILED: {e}")
    rec["ts"] = time.strftime("%Y-%m-%dT%H:%M:%S+00:00", time.gmtime())
    with open(log_path, "a") as f:
        f.write(json.dumps(rec) + "\n")
