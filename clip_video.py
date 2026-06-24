#!/usr/bin/env python3
"""
视频剪辑工具：基于 rally_segments.json 裁剪出回合视频。

用法:
  python clip_video.py <video_path> <output_dir> [--mode clips|highlights|timeline]

模式:
  clips      - 每个回合单独输出为独立视频文件
  highlights - 将所有回合拼接为一个集锦视频
  timeline   - 输出回合时间线 JSON（供外部使用）
"""

import json, os, subprocess, sys, tempfile
from pathlib import Path


def find_ffmpeg():
    for path in [os.path.expanduser("~/.local/bin/ffmpeg"), "ffmpeg"]:
        try:
            subprocess.run([path, "-version"], capture_output=True, timeout=5)
            return path
        except Exception:
            continue
    raise RuntimeError("ffmpeg not found")


def clip_clips(video_path, rallies, output_dir, fps, padding_sec=1.0):
    """每个回合单独输出为一个视频文件"""
    os.makedirs(output_dir, exist_ok=True)
    ffmpeg = find_ffmpeg()
    output_files = []

    for r in rallies:
        start = max(0, r["start_sec"] - padding_sec)
        end = r["end_sec"] + padding_sec
        duration = end - start
        out_path = os.path.join(output_dir, f"rally_{r['id']:03d}.mp4")

        subprocess.run([
            ffmpeg, "-y", "-ss", str(start), "-i", video_path,
            "-t", str(duration), "-c:v", "libx264", "-preset", "fast",
            "-crf", "23", "-movflags", "+faststart", out_path,
        ], capture_output=True)

        if os.path.exists(out_path) and os.path.getsize(out_path) > 0:
            output_files.append(out_path)
            print(f"  Rally {r['id']:3d}: {start:.1f}s - {end:.1f}s → {out_path}")

    return output_files


def clip_highlights(video_path, rallies, output_dir, fps, padding_sec=2.0):
    """将所有回合拼接为一个集锦视频"""
    os.makedirs(output_dir, exist_ok=True)
    ffmpeg = find_ffmpeg()

    # Step 1: 切出每个回合
    clips = []
    with tempfile.TemporaryDirectory() as tmp:
        for i, r in enumerate(rallies):
            start = max(0, r["start_sec"] - padding_sec)
            end = r["end_sec"] + padding_sec
            clip_path = os.path.join(tmp, f"clip_{i:04d}.mp4")
            subprocess.run([
                ffmpeg, "-y", "-ss", str(start), "-i", video_path,
                "-t", str(end - start), "-c:v", "libx264", "-preset", "fast",
                "-crf", "23", clip_path,
            ], capture_output=True)
            if os.path.exists(clip_path):
                clips.append(clip_path)

        if not clips:
            print("没有找到回合片段")
            return []

        # Step 2: 用 concat demuxer 拼接
        concat_list = os.path.join(tmp, "concat.txt")
        with open(concat_list, "w") as f:
            for clip in clips:
                f.write(f"file '{clip}'\n")

        output_path = os.path.join(output_dir, "highlights.mp4")
        subprocess.run([
            ffmpeg, "-y", "-f", "concat", "-safe", "0", "-i", concat_list,
            "-c:v", "libx264", "-preset", "fast", "-crf", "23",
            "-movflags", "+faststart", output_path,
        ], capture_output=True)

        if os.path.exists(output_path):
            print(f"集锦视频: {output_path} ({len(clips)} 个回合, {sum(r['end_sec']-r['start_sec']+padding_sec*2 for r in rallies):.0f}秒)")
            return [output_path]

    return []


def clip_timeline(video_path, rallies, output_dir, fps):
    """输出回合时间线 JSON"""
    os.makedirs(output_dir, exist_ok=True)
    timeline = {
        "video": video_path,
        "fps": fps,
        "total_rallies": len(rallies),
        "rallies": rallies,
    }
    out_path = os.path.join(output_dir, "rally_timeline.json")
    with open(out_path, "w") as f:
        json.dump(timeline, f, ensure_ascii=False, indent=2)
    print(f"时间线: {out_path}")
    return [out_path]


def main():
    import argparse
    p = argparse.ArgumentParser(description="回合视频剪辑")
    p.add_argument("--video-path", required=True, help="原始视频路径")
    p.add_argument("--rally-file", required=True, help="rally_segments.json 路径")
    p.add_argument("--output-dir", required=True, help="输出目录")
    p.add_argument("--mode", default="highlights", choices=["clips", "highlights", "timeline"])
    p.add_argument("--padding", type=float, default=1.5, help="回合前后保留秒数")
    args = p.parse_args()

    with open(args.rally_file) as f:
        data = json.load(f)

    rallies = data["rallies"]
    fps = data["fps"]
    print(f"视频: {args.video_path}")
    print(f"模式: {args.mode}, 回合数: {len(rallies)}, 帧率: {fps}")

    if args.mode == "clips":
        clip_clips(args.video_path, rallies, args.output_dir, fps, args.padding)
    elif args.mode == "highlights":
        clip_highlights(args.video_path, rallies, args.output_dir, fps, args.padding)
    elif args.mode == "timeline":
        clip_timeline(args.video_path, rallies, args.output_dir, fps)


if __name__ == "__main__":
    main()
