#!/usr/bin/env python3
"""Good-Badminton Web 前端"""

import os, sys, json, time, subprocess, glob, shutil
from pathlib import Path
from flask import Flask, request, jsonify, send_file

PROJECT_ROOT = Path(__file__).resolve().parent
VIDEOS = PROJECT_ROOT / 'videos'
OUTPUTS = PROJECT_ROOT / 'outputs'
TEMPLATES = PROJECT_ROOT / 'templates'
WEIGHTS = PROJECT_ROOT / 'weights'

VIDEOS.mkdir(exist_ok=True)
OUTPUTS.mkdir(exist_ok=True)
TEMPLATES.mkdir(exist_ok=True)

app = Flask(__name__)
app.config['MAX_CONTENT_LENGTH'] = 1024 * 1024 * 1024

# ── Job store ────────────────────────────────────────────────────────────
jobs = {}
_venv_python = str(PROJECT_ROOT / '.venv' / 'bin' / 'python3')


# ═══════════════════════════════════════════════════════════════════════════
# API Routes
# ═══════════════════════════════════════════════════════════════════════════

@app.route('/api/videos')
def api_videos():
    """列出所有视频和对应的分析结果"""
    videos = []
    for ext in ('*.mp4', '*.mov', '*.avi', '*.mkv', '*.webm'):
        for p in sorted(VIDEOS.glob(ext)):
            name = p.stem
            out_dir = OUTPUTS / name
            has_result = (out_dir / 'detections.jsonl').exists()
            has_annotations = (out_dir / 'court_annotations.txt').exists()
            videos.append({
                'name': name,
                'filename': p.name,
                'size_mb': round(p.stat().st_size / 1024 / 1024, 1),
                'has_result': has_result,
                'has_annotations': has_annotations,
                'output_dir': str(out_dir) if has_result else None,
            })
    return jsonify(videos)


@app.route('/api/upload', methods=['POST'])
def api_upload():
    """上传视频"""
    file = request.files.get('file')
    if not file:
        return jsonify({'error': '没有文件'}), 400
    name = file.filename
    if not name:
        return jsonify({'error': '空文件名'}), 400
    save_path = VIDEOS / name
    file.save(str(save_path))
    return jsonify({'ok': True, 'filename': name, 'size_mb': round(save_path.stat().st_size / 1024 / 1024, 1)})


@app.route('/api/detect', methods=['POST'])
def api_detect():
    """自动检测球场边界"""
    data = request.json
    video_name = data.get('video')
    if not video_name:
        return jsonify({'error': '缺少 video 参数'}), 400

    video_path = VIDEOS / video_name
    if not video_path.exists():
        return jsonify({'error': '视频不存在'}), 404

    save_dir = OUTPUTS / video_path.stem
    save_dir.mkdir(exist_ok=True)

    env = os.environ.copy()
    env['PATH'] = f"{os.path.expanduser('~/.local/bin')}:{env.get('PATH', '')}"

    cmd = [_venv_python, str(PROJECT_ROOT / 'court_detect.py'), str(video_path), str(save_dir)]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=60, cwd=str(PROJECT_ROOT), env=env)

        # 找到 JSON 块的起止位置
        stdout = r.stdout
        json_start = stdout.rfind('{')
        if json_start >= 0:
            result = json.loads(stdout[json_start:])
            return jsonify(result)

        return jsonify({
            'success': False,
            'error': '无法解析球场检测输出',
            'stdout': r.stdout[-500:],
            'stderr': r.stderr[-500:],
        }), 500
    except subprocess.TimeoutExpired:
        return jsonify({'success': False, 'error': '球场检测超时'}), 500
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/manual-annotate', methods=['POST'])
def api_manual_annotate():
    """保存手动标注的球场角点，并生成预览图"""
    data = request.json
    video_name = data.get('video')
    corners = data.get('corners')  # [[x1,y1], [x2,y2], [x3,y3], [x4,y4]]

    if not video_name or not corners or len(corners) != 4:
        return jsonify({'error': '需要4个角点'}), 400

    import cv2
    import numpy as np

    save_dir = OUTPUTS / Path(video_name).stem
    save_dir.mkdir(exist_ok=True)

    # 查找模板图片
    template = TEMPLATES / f'_auto_{Path(video_name).stem}.png'
    if not template.exists():
        for t in TEMPLATES.iterdir():
            if t.stem.startswith(f'_auto_{Path(video_name).stem}'):
                template = t
                break
        else:
            return jsonify({'error': '找不到模板图'}), 400

    img = cv2.imread(str(template))
    if img is None:
        return jsonify({'error': '无法读取模板图'}), 400

    h, w = img.shape[:2]

    # 计算 ROI
    pts = np.array(corners, dtype=np.float32)
    x_min, y_min = pts.min(axis=0)
    x_max, y_max = pts.max(axis=0)
    pad_x = (x_max - x_min) * 0.2
    pad_y = (y_max - y_min) * 0.2
    roi = [(max(0, int(x_min - pad_x)), max(0, int(y_min - pad_y))),
           (min(w, int(x_max + pad_x)), min(h, int(y_max + pad_y)))]

    # order: top-left, top-right, bottom-right, bottom-left
    top_y = (pts[0][1] + pts[1][1]) / 2
    bottom_y = (pts[2][1] + pts[3][1]) / 2
    mid_height = int((top_y + bottom_y) / 2)

    # 写入标注文件
    with open(str(save_dir / 'court_annotations.txt'), 'w') as f:
        f.write(f"corners={corners}\n")
        f.write(f"roi_corners={roi}\n")
        f.write(f"mid_height={mid_height}\n")

    # 生成带手动标注的预览图
    preview_img = img.copy()
    # 缩放到合理大小用于预览
    max_dim = 1200
    scale_val = min(1.0, max_dim / max(w, h))
    small = cv2.resize(preview_img, (int(w*scale_val), int(h*scale_val)))
    sc = [(int(x*scale_val), int(y*scale_val)) for x, y in corners]
    sr = [(int(x*scale_val), int(y*scale_val)) for x, y in roi]

    # 画四边形
    cv2.polylines(small, [np.array(sc, dtype=np.int32)], True, (0, 255, 0), 3)
    # 画角点和数字
    colors = [(0, 0, 255), (0, 255, 0), (255, 0, 0), (0, 255, 255)]
    for i, (pt, col) in enumerate(zip(sc, colors)):
        cv2.circle(small, pt, 8, col, -1)
        cv2.putText(small, str(i+1), (pt[0]+12, pt[1]-8), cv2.FONT_HERSHEY_SIMPLEX, 0.85, (255,255,255), 2)
    # 画 ROI 框
    if sr:
        cv2.rectangle(small, sr[0], sr[1], (255, 0, 0), 2)

    # 写标题
    cv2.rectangle(small, (0, 0), (small.shape[1], 40), (0, 0, 0), -1)
    cv2.putText(small, "Manual annotation - corners saved", (12, 28), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (255,255,255), 2)

    preview_path = str(save_dir / 'manual_court_preview.png')
    cv2.imwrite(preview_path, small)

    return jsonify({
        'ok': True,
        'corners': corners,
        'roi': roi,
        'mid_height': mid_height,
        'preview_path': f'/api/output/{Path(video_name).stem}/manual_court_preview.png',
    })


@app.route('/api/analyze', methods=['POST'])
def api_analyze():
    """启动视频分析（后台子进程）"""
    data = request.json
    video_name = data.get('video')
    language = data.get('language', 'zh')
    pose_family = data.get('pose_family', 'yolo-pose')

    video_path = VIDEOS / video_name
    if not video_path.exists():
        return jsonify({'error': '视频不存在'}), 404

    save_dir = OUTPUTS / video_path.stem
    save_dir.mkdir(exist_ok=True)

    # 检查球场标注
    has_annotations = (save_dir / 'court_annotations.txt').exists()
    has_template = (TEMPLATES / f'_auto_{video_path.stem}.png').exists()

    template_arg = str(TEMPLATES / f'_auto_{video_path.stem}.png') if has_template else str(TEMPLATES / 'demo.png')

    env = os.environ.copy()
    env['PATH'] = f"{PROJECT_ROOT / '.local' / 'bin'}:{env.get('PATH', '')}"

    cmd = [
        _venv_python, str(PROJECT_ROOT / 'main.py'),
        '--video-path', str(video_path),
        '--template-path', template_arg,
        '--output-dir', str(save_dir),
        '--display', 'false',
        '--language', language,
        '--pose-family', pose_family,
        '--yolo-pose-model', str(WEIGHTS / 'yolo11n-pose.pt'),
        '--ball-model', str(WEIGHTS / 'yolo11s-ball.pt'),
        '--visualize-positions', 'true',
        '--performance-stats',
    ]

    job_id = video_path.stem
    jobs[job_id] = {
        'status': 'pending',
        'progress': 0,
        'message': '准备启动...',
        'video_name': video_name,
        'save_dir': str(save_dir),
    }

    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            text=True, cwd=str(PROJECT_ROOT), env=env)
    jobs[job_id]['proc'] = proc
    jobs[job_id]['status'] = 'running'

    # 启动进度跟踪线程
    import threading

    def track_progress():
        job = jobs[job_id]
        detections_file = os.path.join(job['save_dir'], 'detections.jsonl')

        # 先从视频获取总帧数
        total_frames = 100  # fallback
        try:
            import cv2
            cap = cv2.VideoCapture(str(video_path))
            total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
            cap.release()
        except:
            pass

        while job['status'] == 'running' and proc.poll() is None:
            # 通过 detections.jsonl 行数估算进度
            if os.path.exists(detections_file):
                try:
                    with open(detections_file) as f:
                        count = sum(1 for _ in f)
                    pct = min(99, max(0, int(count / total_frames * 100)))
                except:
                    count = 0
                    pct = 0
                job['progress'] = pct
                job['message'] = f'分析中... {count}/{total_frames} 帧'
            time.sleep(1.5)

        proc.wait()

        if proc.returncode == 0:
            job['status'] = 'reencoding'
            job['progress'] = 99
            job['message'] = '正在转码为浏览器兼容格式...'

            # FFmpeg 转码为 H.264（浏览器兼容）
            vs = video_path.stem
            sd = str(save_dir)
            raw_video = os.path.join(sd, f'detect_{vs}.mp4')
            h264_video = os.path.join(sd, f'detect_{vs}_h264.mp4')
            ffmpeg_bin = os.path.expanduser('~/.local/bin/ffmpeg')

            try:
                subprocess.run([
                    ffmpeg_bin, '-y', '-i', raw_video,
                    '-c:v', 'libx264', '-preset', 'fast', '-crf', '23',
                    '-movflags', '+faststart', h264_video,
                ], check=True, capture_output=True, timeout=300)
                # 用 H.264 版本替换
                if os.path.exists(h264_video) and os.path.getsize(h264_video) > 0:
                    os.replace(h264_video, raw_video)
            except Exception as e:
                print(f'FFmpeg re-encode failed: {e}, using original video')
                # 即使转码失败也继续，原始视频可能在某些播放器能播放

            job['status'] = 'completed'
            job['progress'] = 100
            job['message'] = '分析完成!'

            # 收集结果
            vs = video_path.stem
            sd = str(save_dir)
            pos_dir = os.path.join(sd, 'position_visualizations')
            rally_file = os.path.join(sd, 'rally_segments.json')
            rally_count = 0
            if os.path.exists(rally_file):
                try:
                    with open(rally_file) as rf:
                        rally_count = len(json.load(rf).get('rallies', []))
                except:
                    pass

            job['result'] = {
                'video_name': vs,
                'output_video': f'/api/output/{vs}/detect_{vs}.mp4',
                'detections': f'/api/output/{vs}/detections.jsonl',
                'heatmap': f'/api/output/{vs}/position_visualizations/heatmaps/match_heatmap.png',
                'scatter': f'/api/output/{vs}/position_visualizations/scatter_plots/match_scatter.png',
                'preview': f'/api/output/{vs}/auto_court_preview.png',
                'rally_count': rally_count,
            }
        else:
            job['status'] = 'error'
            job['message'] = f'分析失败 (exit={proc.returncode})'

    threading.Thread(target=track_progress, daemon=True).start()
    return jsonify({'ok': True, 'job_id': job_id})


@app.route('/api/status/<job_id>')
def api_status(job_id):
    """查询分析进度"""
    job = jobs.get(job_id)
    if not job:
        return jsonify({'status': 'not_found'}), 404
    return jsonify({
        'status': job.get('status'),
        'progress': job.get('progress', 0),
        'message': job.get('message', ''),
        'result': job.get('result'),
    })


@app.route('/api/clip', methods=['POST'])
def api_clip():
    """生成回合剪辑视频"""
    data = request.json
    video_name = data.get('video')
    mode = data.get('mode', 'highlights')
    padding = data.get('padding', 1.5)

    video_path = VIDEOS / video_name
    save_dir = OUTPUTS / video_path.stem
    rally_file = save_dir / 'rally_segments.json'

    if not rally_file.exists():
        return jsonify({'error': '还没有回合检测数据，请先运行分析'}), 400

    clip_output_dir = save_dir / 'clips'
    clip_output_dir.mkdir(exist_ok=True)

    env = os.environ.copy()
    env['PATH'] = f"{os.path.expanduser('~/.local/bin')}:{env.get('PATH', '')}"

    cmd = [
        _venv_python, str(PROJECT_ROOT / 'clip_video.py'),
        '--video-path', str(video_path),
        '--rally-file', str(rally_file),
        '--output-dir', str(clip_output_dir),
        '--mode', mode,
        '--padding', str(padding),
    ]

    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=300, cwd=str(PROJECT_ROOT), env=env)
        print(r.stdout)

        # 查找生成的剪辑文件
        clips = sorted(clip_output_dir.glob('*.mp4'))
        result = {
            'ok': True,
            'mode': mode,
            'clips': [{
                'name': c.name,
                'url': f'/api/output/{video_path.stem}/clips/{c.name}',
                'size_mb': round(c.stat().st_size / 1024 / 1024, 1),
            } for c in clips],
        }
        return jsonify(result)
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/output/<video_name>/<path:subpath>')
def serve_output(video_name, subpath):
    """提供输出文件，支持视频 Range 请求"""
    filepath = OUTPUTS / video_name / subpath
    if not filepath.exists():
        return jsonify({'error': f'文件不存在: {subpath}'}), 404
    if filepath.suffix in ('.mp4', '.webm'):
        return send_file(str(filepath), mimetype='video/mp4', conditional=True)
    if filepath.suffix == '.jsonl':
        return send_file(str(filepath), mimetype='application/x-ndjson', conditional=True)
    return send_file(str(filepath), conditional=True)


@app.route('/api/template/<video_name>')
def serve_template_image(video_name):
    """提供模板图片（用于手动标注）"""
    template = TEMPLATES / f'_auto_{video_name}.png'
    if template.exists():
        return send_file(str(template))
    # fallback: 尝试其他格式
    for t in TEMPLATES.iterdir():
        if t.stem.startswith(f'_auto_{video_name}'):
            return send_file(str(t))
    return jsonify({'error': '模板不存在'}), 404


@app.route('/api/output/<video_name>/<path:subpath>', methods=['DELETE'])
def delete_output(video_name, subpath):
    """删除某个输出文件或整个输出目录"""
    filepath = OUTPUTS / video_name / subpath
    if filepath.is_dir():
        shutil.rmtree(str(filepath))
    elif filepath.exists():
        filepath.unlink()
    return jsonify({'ok': True})


# ═══════════════════════════════════════════════════════════════════════════
# Static page
# ═══════════════════════════════════════════════════════════════════════════

@app.route('/')
def index():
    return (PROJECT_ROOT / 'web_ui.html').read_text(encoding='utf-8')


if __name__ == '__main__':
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument('--port', type=int, default=5050)
    p.add_argument('--host', default='127.0.0.1')
    args = p.parse_args()

    print(f'''
╔══════════════════════════════════════════╗
║  🏸  Good Badminton Web Frontend       ║
║  打开浏览器访问: http://{args.host}:{args.port}  ║
╚══════════════════════════════════════════╝
''')
    app.run(host=args.host, port=args.port, debug=False)
