#!/usr/bin/env python3
"""Generate 5 neutral, royalty-free placeholder B-roll loops for the style
engine. These are abstract procedural textures (no stock/reference footage
involved) standing in for: archive, crowd/society, clock/time, clouds/sky,
and a blurred silhouette derived from the user's own talking-head frame.

Swap any of the output files for real B-roll later -- apply_style.py only
cares about the file path in config.json, not how it was made.
"""
import argparse
import os
import subprocess

LOOP_SECONDS = 6  # long enough that any single timeline segment can pull from it


def run(cmd):
    subprocess.run(cmd, check=True)


def gen_archive(out_path, w, h, fps):
    # Grainy static texture -> reads as scratched archival film once sepia-washed.
    # Pure noise compresses terribly at low CRF, so use a higher CRF and a
    # shorter loop -- it's supposed to look grainy, artifacts are invisible.
    run([
        "ffmpeg", "-y", "-f", "lavfi",
        "-i", f"color=c=0x808080:s={w}x{h}:d=4:r={fps}",
        "-vf", "noise=alls=45:allf=t+u,eq=contrast=1.2",
        "-pix_fmt", "yuv420p", "-c:v", "libx264", "-preset", "veryfast", "-crf", "30",
        out_path,
    ])


def gen_crowd(out_path, w, h, fps):
    # Conway's-life field at low res, blurred back up -> organic moving blobs,
    # reads as a distant crowd/flags ripple once green-tinted.
    small = 100
    run([
        "ffmpeg", "-y", "-f", "lavfi",
        "-i", f"life=size={small}x{small}:rate={fps}:mold=20:ratio=0.22:mold_color=gray",
        "-t", str(LOOP_SECONDS),
        "-vf", f"scale={w}:{h}:flags=bilinear,gblur=sigma=3",
        "-pix_fmt", "yuv420p", "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
        out_path,
    ])


def gen_clock(out_path, w, h, fps):
    # Rotating radial sweep line -> literal clock-hand / radar motion.
    expr = (
        "if(lt(mod(atan2(Y-H/2\\,X-W/2)-2*PI*T/3\\,2*PI)\\,0.18)\\,255\\,15)"
    )
    run([
        "ffmpeg", "-y", "-f", "lavfi",
        "-i", f"color=c=black:s={w}x{h}:d={LOOP_SECONDS}:r={fps}",
        "-vf", f"geq=lum='{expr}':cb=128:cr=128",
        "-pix_fmt", "yuv420p", "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
        out_path,
    ])


def gen_clouds(out_path, w, h, fps):
    # Slow drifting soft gradient -> atmospheric sky/clouds mood once cool-tinted.
    run([
        "ffmpeg", "-y", "-f", "lavfi",
        "-i", f"gradients=s={w}x{h}:d={LOOP_SECONDS}:r={fps}:type=radial:speed=0.02:nb_colors=3",
        "-pix_fmt", "yuv420p", "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
        out_path,
    ])


def gen_blur(out_path, w, h, fps, main_clip, mid_time):
    # A heavily blurred, slow-zooming freeze of the user's own footage --
    # not reused as content, just an abstracted silhouette for the silent
    # transition beat.
    frame_path = out_path + ".frame.png"
    run([
        "ffmpeg", "-y", "-ss", str(mid_time), "-i", main_clip,
        "-vframes", "1", frame_path,
    ])
    frames = int(LOOP_SECONDS * fps)
    run([
        "ffmpeg", "-y", "-loop", "1", "-i", frame_path, "-t", str(LOOP_SECONDS),
        "-vf",
        f"scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h},"
        f"gblur=sigma=20,eq=brightness=0.22:contrast=1.15,"
        f"zoompan=z='min(zoom+0.0012,1.15)':d={frames}:s={w}x{h}:fps={fps}",
        "-pix_fmt", "yuv420p", "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
        out_path,
    ])
    os.remove(frame_path)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--width", type=int, required=True)
    ap.add_argument("--height", type=int, required=True)
    ap.add_argument("--fps", type=int, default=30)
    ap.add_argument("--main-clip", required=True, help="used only to derive the blurred silhouette placeholder")
    ap.add_argument("--mid-time", type=float, default=None, help="timestamp in main-clip to grab for the blur placeholder")
    args = ap.parse_args()

    os.makedirs(args.out_dir, exist_ok=True)
    mid = args.mid_time
    if mid is None:
        probe = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", args.main_clip],
            capture_output=True, text=True, check=True,
        )
        mid = float(probe.stdout.strip()) / 2

    gen_archive(os.path.join(args.out_dir, "archive.mp4"), args.width, args.height, args.fps)
    gen_crowd(os.path.join(args.out_dir, "crowd.mp4"), args.width, args.height, args.fps)
    gen_clock(os.path.join(args.out_dir, "clock.mp4"), args.width, args.height, args.fps)
    gen_clouds(os.path.join(args.out_dir, "clouds.mp4"), args.width, args.height, args.fps)
    gen_blur(os.path.join(args.out_dir, "blur.mp4"), args.width, args.height, args.fps, args.main_clip, mid)
    print("done ->", args.out_dir)


if __name__ == "__main__":
    main()
