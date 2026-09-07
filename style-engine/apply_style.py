#!/usr/bin/env python3
"""Config-driven engine that applies the "B&W talking-head + color-washed
glitch-cut B-roll + rotating-color Arabic captions" mechanic to your own
footage.

Nothing here is copied from any reference video: every filter chain below
is written from the *technical description* of the effect (grayscale base,
per-segment color wash, RGB-split glitch at cuts, hard-cut caption color
rotation), not from reference pixels or audio. See README.md for the config
schema and how to swap in real B-roll / real caption text.

Usage:
    python3 apply_style.py --config config.json
"""
import argparse
import json
import os
import shutil
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from captions import render_caption  # noqa: E402

WASH_FILTERS = {
    "bw": "format=gray,eq=contrast=1.25:brightness=0.02,noise=alls=18:allf=t+u",
    "sepia": (
        "hue=s=0,eq=contrast=1.1,"
        "colorchannelmixer=.393:.769:.189:0:.349:.686:.168:0:.272:.534:.131:0,"
        "noise=alls=12:allf=t+u"
    ),
    "sepia_light": (
        "hue=s=0,eq=contrast=1.05:brightness=0.05,"
        "colorchannelmixer=.5:.6:.2:0:.4:.55:.2:0:.3:.45:.15:0,"
        "noise=alls=10:allf=t+u"
    ),
    "green_tint": (
        "hue=s=0,eq=contrast=1.15,colorchannelmixer=rr=0.65:gg=1.0:bb=0.65,"
        "noise=alls=10:allf=t+u"
    ),
    "cool_blue": (
        "hue=s=0,eq=contrast=1.1:brightness=0.04,"
        "colorchannelmixer=rr=0.8:gg=0.95:bb=1.2,noise=alls=8:allf=t+u"
    ),
    "overexposed": (
        "hue=s=0,eq=contrast=1.5:brightness=0.4,rgbashift=rh=3:bh=-3,"
        "noise=alls=6:allf=t+u"
    ),
}

BROLL_DEFAULT_WASH = {
    "archive": "sepia",
    "crowd": "green_tint",
    "clock": "sepia_light",
    "clouds": "cool_blue",
    "blur": "overexposed",
}

BURST_THRESHOLD = 0.5  # segments shorter than this count as "burst" cuts


def run(cmd, quiet=True):
    kwargs = {}
    if quiet:
        kwargs["stdout"] = subprocess.DEVNULL
        kwargs["stderr"] = subprocess.DEVNULL
    subprocess.run(cmd, check=True, **kwargs)


def ffprobe_json(path):
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-print_format", "json",
         "-show_format", "-show_streams", path],
        capture_output=True, text=True, check=True,
    )
    return json.loads(out.stdout)


def cover_scale(w, h):
    return f"scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h}"


def render_main_segment(main_clip, start, end, w, h, fps, wash, out_path):
    vf = f"{cover_scale(w,h)},fps={fps},{WASH_FILTERS[wash]}"
    run([
        "ffmpeg", "-y", "-ss", f"{start:.3f}", "-i", main_clip,
        "-t", f"{end-start:.3f}", "-an",
        "-vf", vf, "-pix_fmt", "yuv420p",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "18",
        out_path,
    ])


def render_broll_segment(src_path, cursor, duration, w, h, fps, wash, out_path, loop_len):
    offset = cursor % max(loop_len - duration - 0.05, 0.01)
    vf = f"{cover_scale(w,h)},fps={fps},{WASH_FILTERS[wash]}"
    run([
        "ffmpeg", "-y", "-stream_loop", "-1", "-ss", f"{offset:.3f}", "-i", src_path,
        "-t", f"{duration:.3f}", "-an",
        "-vf", vf, "-pix_fmt", "yuv420p",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "18",
        out_path,
    ])


def extract_frame(video_path, at_start, out_png):
    if at_start:
        run(["ffmpeg", "-y", "-i", video_path, "-vframes", "1", out_png])
    else:
        run(["ffmpeg", "-y", "-sseof", "-0.05", "-i", video_path, "-update", "1",
             "-vframes", "1", out_png])


def render_glitch_half(frame_png, duration, fps, w, h, out_path, strong, flash=False):
    if strong:
        chain = "rgbashift=rh=16:gh=-8:bh=-16:rv=6:bv=-6,noise=alls=70:allf=t+u,eq=contrast=1.4"
        if flash:
            chain = "eq=brightness=0.55:contrast=0.5," + chain
    else:
        chain = "rgbashift=rh=5:gh=-2:bh=-5,noise=alls=25:allf=t+u"
    vf = f"scale={w}:{h},{chain},fps={fps}"
    run([
        "ffmpeg", "-y", "-loop", "1", "-i", frame_png, "-t", f"{duration:.3f}",
        "-vf", vf, "-pix_fmt", "yuv420p",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "18",
        out_path,
    ])


def build_video_track(cfg, tmpdir, w, h, fps, total_dur):
    timeline = cfg["timeline"]
    broll_dir = cfg.get("broll_dir", "assets/broll_placeholders")
    broll_sources = cfg.get("broll_sources", {})
    glitch_ms = cfg.get("glitch_default_ms", 220)

    # sanity: contiguous timeline covering [0, total_dur]
    assert abs(timeline[0]["start"] - 0.0) < 0.05, "timeline must start at 0"
    for i in range(len(timeline) - 1):
        assert abs(timeline[i]["end"] - timeline[i + 1]["start"]) < 0.01, (
            f"gap/overlap between segment {i} and {i+1}"
        )

    n = len(timeline)
    boundary_glitch = []  # duration per boundary i (between seg i and i+1)
    for i in range(n - 1):
        raw_a = timeline[i]["end"] - timeline[i]["start"]
        raw_b = timeline[i + 1]["end"] - timeline[i + 1]["start"]
        burst = raw_a < BURST_THRESHOLD or raw_b < BURST_THRESHOLD
        dur = (glitch_ms / 1000.0) * (1.25 if burst else 1.0)
        # never let a glitch reservation eat more than 60% of either
        # neighboring segment -- keeps very short burst-cluster cuts from
        # going negative on content duration.
        dur = min(dur, 0.6 * raw_a, 0.6 * raw_b)
        dur = max(dur, 0.06)
        boundary_glitch.append({"dur": dur, "burst": burst})

    # content windows after reserving half-glitch on each shared boundary
    content = []
    for i, seg in enumerate(timeline):
        cs = seg["start"]
        ce = seg["end"]
        if i > 0:
            cs += boundary_glitch[i - 1]["dur"] / 2
        if i < n - 1:
            ce -= boundary_glitch[i]["dur"] / 2
        content.append((cs, ce))

    cursors = {k: 0.0 for k in broll_sources}
    pieces = []

    for i, seg in enumerate(timeline):
        cs, ce = content[i]
        dur = max(ce - cs, 0.02)
        seg_path = os.path.join(tmpdir, f"seg_{i:03d}.mp4")
        if seg["source"] == "main":
            render_main_segment(cfg["main_clip"], cs, ce, w, h, fps, "bw", seg_path)
        else:
            key = seg["source"]
            wash = seg.get("wash", BROLL_DEFAULT_WASH.get(key, "sepia"))
            src_path = os.path.join(broll_dir, broll_sources[key])
            loop_len = ffprobe_duration(src_path)
            render_broll_segment(src_path, cursors[key], dur, w, h, fps, wash, seg_path, loop_len)
            cursors[key] += dur + 0.37
        pieces.append(seg_path)

        if i < n - 1:
            g = boundary_glitch[i]
            half = g["dur"] / 2
            last_png = os.path.join(tmpdir, f"bound_{i:03d}_a.png")
            extract_frame(seg_path, at_start=False, out_png=last_png)
            # first frame of the NEXT segment doesn't exist yet -- deferred to the
            # second pass below, once every seg_*.mp4 has been rendered.
            pieces.append(("GLITCH_A", i, last_png, half, g["burst"]))
            pieces.append(("GLITCH_B", i, half, g["burst"]))

    # second pass: the next segment's first frame is only available now that
    # every seg_*.mp4 from the loop above has been rendered.
    final_pieces = []
    for item in pieces:
        if isinstance(item, tuple) and item[0] == "GLITCH_B":
            _, boundary_i, half, burst = item
            first_png = os.path.join(tmpdir, f"bound_{boundary_i:03d}_b.png")
            next_seg_path = os.path.join(tmpdir, f"seg_{boundary_i+1:03d}.mp4")
            extract_frame(next_seg_path, at_start=True, out_png=first_png)
            out_path = os.path.join(tmpdir, f"glitchB_{boundary_i:03d}.mp4")
            render_glitch_half(first_png, half, fps, w, h, out_path, strong=True, flash=False)
            final_pieces.append(out_path)
        elif isinstance(item, tuple) and item[0] == "GLITCH_A":
            _, boundary_i, last_png, half, burst = item
            out_path = os.path.join(tmpdir, f"glitchA_{boundary_i:03d}.mp4")
            render_glitch_half(last_png, half, fps, w, h, out_path, strong=True, flash=burst)
            final_pieces.append(out_path)
        else:
            final_pieces.append(item)

    list_path = os.path.join(tmpdir, "concat_list.txt")
    with open(list_path, "w") as f:
        for p in final_pieces:
            f.write(f"file '{os.path.abspath(p)}'\n")

    silent_video = os.path.join(tmpdir, "silent_video.mp4")
    run([
        "ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", list_path,
        "-c", "copy", silent_video,
    ])
    return silent_video


def ffprobe_duration(path):
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", path],
        capture_output=True, text=True, check=True,
    )
    return float(out.stdout.strip())


def build_sfx_track(cut_times, total_dur, tmpdir):
    sfx_src = os.path.join(tmpdir, "sfx_hit.wav")
    run([
        "ffmpeg", "-y", "-f", "lavfi",
        "-i", "anoisesrc=d=0.12:c=white:a=0.7",
        "-af", "afade=out:st=0.05:d=0.07,highpass=f=800",
        sfx_src,
    ])
    return sfx_src


def mux_final(cfg, silent_video, tmpdir, total_dur, w, h, fps, out_path, cut_times):
    font_path = cfg.get("font_path", "/usr/share/fonts/opentype/lemonada/Lemonada-Bold.otf")
    captions = cfg.get("captions", [])
    cap_pngs = []
    for i, cap in enumerate(captions):
        png_path = os.path.join(tmpdir, f"cap_{i:03d}.png")
        render_caption(cap["text"], cap.get("color", "white"), w, h, font_path, png_path)
        cap_pngs.append((png_path, cap["start"], cap["end"]))

    inputs = ["-i", silent_video]
    for png_path, _, _ in cap_pngs:
        inputs += ["-i", png_path]
    audio_input_idx = 1 + len(cap_pngs)
    inputs += ["-i", cfg["main_clip"]]

    use_sfx = cfg.get("sfx", True) and cut_times
    sfx_idx = None
    if use_sfx:
        sfx_src = build_sfx_track(cut_times, total_dur, tmpdir)
        inputs += ["-i", sfx_src]
        sfx_idx = audio_input_idx + 1

    filter_parts = ["[0:v]null[v0]"]
    last_label = "v0"
    for i, (_, start, end) in enumerate(cap_pngs):
        out_label = f"v{i+1}"
        filter_parts.append(
            f"[{last_label}][{i+1}:v]overlay=enable='between(t,{start:.3f},{end:.3f})'[{out_label}]"
        )
        last_label = out_label

    if use_sfx:
        n_cuts = len(cut_times)
        filter_parts.append(
            f"[{audio_input_idx}:a]aformat=sample_rates=48000:channel_layouts=stereo[voice]"
        )
        filter_parts.append(
            f"[{sfx_idx}:a]asplit={n_cuts}" + "".join(f"[sfxsrc{j}]" for j in range(n_cuts))
        )
        mix_labels = ["voice"]
        for j, t in enumerate(cut_times):
            ms = max(int(t * 1000), 0)
            filter_parts.append(
                f"[sfxsrc{j}]adelay={ms}:all=1,volume=0.35,"
                f"aformat=sample_rates=48000:channel_layouts=stereo[sfxd{j}]"
            )
            mix_labels.append(f"sfxd{j}")
        mix_inputs = "".join(f"[{lbl}]" for lbl in mix_labels)
        filter_parts.append(
            f"{mix_inputs}amix=inputs={len(mix_labels)}:duration=first:normalize=0[aout]"
        )
        audio_map = "[aout]"
    else:
        audio_map = f"{audio_input_idx}:a"

    filter_complex = ";".join(filter_parts)

    cmd = ["ffmpeg", "-y"] + inputs + [
        "-filter_complex", filter_complex,
        "-map", f"[{last_label}]",
        "-map", audio_map,
        "-t", f"{total_dur:.3f}",
        "-c:v", "libx264", "-preset", "medium", "-crf", "23",
        "-c:a", "aac", "-b:a", "160k",
        out_path,
    ]
    run(cmd, quiet=False)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", required=True)
    ap.add_argument("--keep-temp", action="store_true")
    args = ap.parse_args()

    cfg = json.load(open(args.config, encoding="utf-8"))
    base_dir = os.path.dirname(os.path.abspath(args.config))
    if not os.path.isabs(cfg["main_clip"]):
        cfg["main_clip"] = os.path.join(base_dir, cfg["main_clip"])
    if not os.path.isabs(cfg.get("broll_dir", "")):
        cfg["broll_dir"] = os.path.join(base_dir, cfg.get("broll_dir", "assets/broll_placeholders"))

    probe = ffprobe_json(cfg["main_clip"])
    vstream = next(s for s in probe["streams"] if s["codec_type"] == "video")
    src_w, src_h = int(vstream["width"]), int(vstream["height"])
    total_dur = float(probe["format"]["duration"])

    canvas = cfg.get("canvas")
    if canvas:
        w, h = map(int, canvas.lower().split("x"))
    else:
        w, h = src_w, src_h
    fps = cfg.get("fps", 30)

    out_path = cfg.get("output", "output/final.mp4")
    if not os.path.isabs(out_path):
        out_path = os.path.join(base_dir, out_path)
    os.makedirs(os.path.dirname(out_path), exist_ok=True)

    tmpdir = os.path.join(base_dir, ".tmp_render")
    if os.path.exists(tmpdir):
        shutil.rmtree(tmpdir)
    os.makedirs(tmpdir)

    cut_times = [seg["start"] for seg in cfg["timeline"][1:]]

    print(f"[1/3] rendering {len(cfg['timeline'])} segments + glitch transitions...")
    silent_video = build_video_track(cfg, tmpdir, w, h, fps, total_dur)

    print(f"[2/3] rendering {len(cfg.get('captions', []))} captions + muxing audio/sfx...")
    mux_final(cfg, silent_video, tmpdir, total_dur, w, h, fps, out_path, cut_times)

    print(f"[3/3] done -> {out_path}")
    if not args.keep_temp:
        shutil.rmtree(tmpdir, ignore_errors=True)


if __name__ == "__main__":
    main()
