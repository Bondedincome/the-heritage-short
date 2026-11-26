"""Wan 2.2 TI2V-5B image-to-video, run inside a Kaggle notebook (GPU T4 x2, Internet ON).

Inputs : a Kaggle dataset made from outputs/kaggle/shots_bundle.zip (jobs.json + keyframes/)
Outputs: /kaggle/working/shots_out/<shot>-<key>.mp4 and runs_kaggle.jsonl (one line per call)

Notebook cells:
    !pip install -q -U "diffusers>=0.35" transformers accelerate ftfy imageio imageio-ffmpeg
    !python /kaggle/input/<your-code-dataset>/wan22_i2v.py --draft      # 480p-ish, fewer steps
    !python /kaggle/input/<your-code-dataset>/wan22_i2v.py              # full settings from jobs.json
Then download /kaggle/working/shots_out and run locally:
    python run.py shots --import path/to/shots_out

Memory plan for a 16 GB T4: encode every prompt first with only the UMT5 text encoder loaded,
free it, then load the transformer + VAE without the text encoder. Model CPU offload and
VAE tiling keep the peak under 16 GB. T4 has no native bf16, so the default dtype is fp16;
if frames come out black/NaN try --dtype bf16 (slower) and note it in EXPERIMENTS.md.
"""
import argparse
import gc
import glob
import json
import os
import socket
import time
import uuid

import torch

ap = argparse.ArgumentParser()
ap.add_argument("--bundle", default=None, help="folder containing jobs.json (auto-detected under /kaggle/input)")
ap.add_argument("--out", default="/kaggle/working/shots_out")
ap.add_argument("--draft", action="store_true", help="lower res + fewer steps for quick looks")
ap.add_argument("--dtype", default="fp16", choices=["fp16", "bf16"])
ap.add_argument("--only", default=None, help="comma-separated shot ids")
args = ap.parse_args()

bundle = args.bundle or os.path.dirname(sorted(glob.glob("/kaggle/input/**/jobs.json", recursive=True))[0])
jobs = json.load(open(os.path.join(bundle, "jobs.json")))["jobs"]
if args.only:
    keep = set(args.only.split(","))
    jobs = [j for j in jobs if j["shot_id"] in keep]
os.makedirs(args.out, exist_ok=True)
log_path = os.path.join(args.out, "runs_kaggle.jsonl")
jobs = [j for j in jobs if not os.path.exists(os.path.join(args.out, j["output"]))]
print(f"{len(jobs)} job(s) to run")
if not jobs:
    raise SystemExit(0)

dtype = torch.float16 if args.dtype == "fp16" else torch.bfloat16
model_id = jobs[0]["model"]
gpu = torch.cuda.get_device_name(0) if torch.cuda.is_available() else "cpu"

from diffusers import AutoencoderKLWan, WanImageToVideoPipeline  # noqa: E402
from diffusers.utils import export_to_video, load_image  # noqa: E402

# ---- phase 1: text encoder only ------------------------------------------------------------
t0 = time.time()
enc = WanImageToVideoPipeline.from_pretrained(model_id, transformer=None, vae=None, torch_dtype=dtype)
enc.text_encoder.to("cuda")
embeds = {}
with torch.no_grad():
    for j in jobs:
        pe, ne = enc.encode_prompt(prompt=j["prompt"], negative_prompt=j["negative_prompt"],
                                   do_classifier_free_guidance=True, device="cuda")
        embeds[j["output"]] = (pe.cpu(), ne.cpu())
del enc
gc.collect(); torch.cuda.empty_cache()
print(f"encoded {len(jobs)} prompt(s) in {time.time() - t0:.0f}s")

# ---- phase 2: transformer + VAE --------------------------------------------------------------
vae = AutoencoderKLWan.from_pretrained(model_id, subfolder="vae", torch_dtype=torch.float32)
pipe = WanImageToVideoPipeline.from_pretrained(model_id, vae=vae, text_encoder=None, tokenizer=None,
                                               torch_dtype=dtype)
pipe.enable_model_cpu_offload()
try:
    pipe.vae.enable_tiling()
except Exception:
    pass


def fit(img, w, h):
    from PIL import ImageOps
    return ImageOps.fit(img.convert("RGB"), (w, h))


for j in jobs:
    w, h, frames, steps = j["width"], j["height"], j["num_frames"], j["steps"]
    if args.draft:
        w, h, frames, steps = w * 2 // 3 // 32 * 32, h * 2 // 3 // 32 * 32, 81, max(12, steps // 2)
    image = fit(load_image(os.path.join(bundle, j["image"])), w, h)
    pe, ne = embeds[j["output"]]
    gen = torch.Generator("cuda").manual_seed(j["seed"])
    call_id = uuid.uuid4().hex
    t0 = time.time()
    rec = {"call_id": call_id, "stage": "shots", "shot_id": j["shot_id"], "provider": "kaggle",
           "model": model_id, "seed": j["seed"], "input_key": j["key"],
           "params": {"w": w, "h": h, "num_frames": frames, "steps": steps, "guidance": j["guidance"],
                      "fps": j["fps"], "dtype": args.dtype, "draft": args.draft, "prompt": j["prompt"][:300]},
           "gpu": gpu, "host": socket.gethostname()}
    try:
        torch.cuda.reset_peak_memory_stats()
        frames_out = pipe(image=image, prompt_embeds=pe.to("cuda"), negative_prompt_embeds=ne.to("cuda"),
                          height=h, width=w, num_frames=frames, num_inference_steps=steps,
                          guidance_scale=j["guidance"], generator=gen).frames[0]
        export_to_video(frames_out, os.path.join(args.out, j["output"]), fps=j["fps"])
        lat = round(time.time() - t0, 1)
        rec.update(status="ok", latency_s=lat, gpu_seconds=lat, cost_usd=0.0,
                   peak_vram_gb=round(torch.cuda.max_memory_allocated() / 1e9, 2), output=j["output"])
        print(f"{j['shot_id']}: {lat}s, peak {rec['peak_vram_gb']} GB")
    except Exception as e:  # keep going; the local pipeline falls back to Ken Burns
        lat = round(time.time() - t0, 1)
        rec.update(status="error", latency_s=lat, gpu_seconds=lat, cost_usd=0.0, error=f"{type(e).__name__}: {e}"[:600])
        print(f"{j['shot_id']} FAILED: {e}")
        gc.collect(); torch.cuda.empty_cache()
    rec["ts"] = time.strftime("%Y-%m-%dT%H:%M:%S+00:00", time.gmtime())
    with open(log_path, "a") as f:
        f.write(json.dumps(rec) + "\n")
