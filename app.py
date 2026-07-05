#!/usr/bin/env python3
"""Good-Badminton Web 前端"""

import os, sys, json, time, subprocess, glob, shutil
from pathlib import Path
from flask import Flask, request, jsonify, send_file
from badminton_analysis.data.writer import write_json
from badminton_analysis.training.plan_generator import generate_plan

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
def _find_venv_python():
    candidates = [
        PROJECT_ROOT / '.venv' / 'Scripts' / 'python.exe',  # Windows
        PROJECT_ROOT / '.venv' / 'bin' / 'python3',          # macOS/Linux
        PROJECT_ROOT / '.venv' / 'bin' / 'python',
    ]
    for c in candidates:
        if c.exists():
            return str(c)
    return sys.executable

_venv_python = _find_venv_python()


def _parse_progress_line(line):
    """Parse 'PROGRESS <pct> <stage>' emitted by a worker. None if not a progress line."""
    parts = line.strip().split()
    if len(parts) == 3 and parts[0] == "PROGRESS":
        try:
            return int(parts[1]), parts[2]
        except ValueError:
            return None
    return None


def _rep_clip_window(contact_frame, fps, padding=1.5):
    """Seconds window (start, duration) around a rep's contact frame, start clamped to 0."""
    if not fps or fps <= 0:
        fps = 30.0
    center = (contact_frame or 0) / fps
    start = max(0.0, center - padding)
    return round(start, 3), round(padding * 2, 3)


_DELETE_SCOPES = ('match', 'posture', 'clips', 'reports', 'all')


def _safe_stem(video_name):
    """Validated video stem for delete operations. None if malformed or unknown."""
    if not video_name or not isinstance(video_name, str):
        return None
    if '/' in video_name or '\\' in video_name or '..' in video_name:
        return None
    if video_name != video_name.strip():
        return None
    if Path(video_name).name != video_name:
        return None
    if (OUTPUTS / video_name).is_dir():
        return video_name
    if VIDEOS.exists() and any(p.is_file() and p.stem == video_name for p in VIDEOS.iterdir()):
        return video_name
    return None


def _delete_groups(stem, scope):
    """Disjoint (key, [paths]) groups a delete scope removes. Paths are files or dirs."""
    out = OUTPUTS / stem
    posture = out / 'posture'

    def match_paths():
        if not out.is_dir():
            return []
        return [p for p in out.iterdir() if p.name not in ('posture', 'thumb.jpg')]

    def source_paths():
        files = []
        if VIDEOS.exists():
            files += [p for p in VIDEOS.iterdir() if p.is_file() and p.stem == stem]
        if TEMPLATES.exists():
            files += [p for p in TEMPLATES.iterdir()
                      if p.is_file() and p.stem == '_auto_' + stem]
        return files

    if scope == 'match':
        return [('match', match_paths())]
    if scope == 'posture':
        return [('posture', [posture] if posture.is_dir() else [])]
    if scope == 'clips':
        rally = out / 'clips'
        rep = posture / 'rep_clips'
        return [('clips_rally', [rally] if rally.is_dir() else []),
                ('clips_rep', [rep] if rep.is_dir() else [])]
    if scope == 'reports':
        reports = ([p for p in posture.iterdir() if p.name.startswith('coach_report_')]
                   if posture.is_dir() else [])
        return [('reports', reports)]
    if scope == 'all':
        thumb = out / 'thumb.jpg'
        return [('source', source_paths()),
                ('match', match_paths()),
                ('posture', [posture] if posture.is_dir() else []),
                ('thumb', [thumb] if thumb.is_file() else [])]
    return []


def _paths_stats(paths):
    """(file count, size MB rounded 1dp) for files and recursive dir contents."""
    files = 0
    size = 0
    for p in paths:
        if p.is_dir():
            for f in p.rglob('*'):
                if f.is_file():
                    files += 1
                    size += f.stat().st_size
        elif p.is_file():
            files += 1
            size += p.stat().st_size
    return files, round(size / 1024 / 1024, 1)


def _running_job_for(stem):
    """Id of any non-terminal analysis job working on this video, else None."""
    active = ('pending', 'running', 'reencoding')
    for job_id, job in list(jobs.items()):
        if job.get('status') not in active:
            continue
        jv = str(job.get('video_name') or '')
        if (job_id == stem or job_id == 'posture_' + stem
                or jv == stem or jv.rsplit('.', 1)[0] == stem):
            return job_id
    return None


# ═══════════════════════════════════════════════════════════════════════════
# API Routes
# ═══════════════════════════════════════════════════════════════════════════

def _video_duration_sec(path):
    """Best-effort video duration in seconds; None if it can't be read."""
    try:
        import cv2
        cap = cv2.VideoCapture(str(path))
        fps = cap.get(cv2.CAP_PROP_FPS) or 0
        frames = cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0
        cap.release()
        if fps > 0 and frames > 0:
            return round(frames / fps, 1)
    except Exception:
        pass
    return None


def _write_first_frame(video_path, dest):
    """Grab frame 0 of the video and save a JPEG at dest. Returns bool."""
    try:
        import cv2
        cap = cv2.VideoCapture(str(video_path))
        ok, frame = cap.read()
        cap.release()
        if not ok or frame is None:
            return False
        h, w = frame.shape[:2]
        scale = min(1.0, 640 / max(w, 1))
        if scale < 1.0:
            frame = cv2.resize(frame, (int(w * scale), int(h * scale)))
        dest.parent.mkdir(parents=True, exist_ok=True)
        return bool(cv2.imwrite(str(dest), frame))
    except Exception:
        return False


def _ensure_thumbnail(video_path, stem):
    """Ensure OUTPUTS/<stem>/thumb.jpg exists; return its Path or None."""
    dest = OUTPUTS / stem / 'thumb.jpg'
    if dest.exists():
        return dest
    if _write_first_frame(video_path, dest):
        return dest
    return None


@app.route('/api/videos')
def api_videos():
    """列出所有视频和对应的分析结果"""
    import datetime
    videos = []
    for ext in ('*.mp4', '*.mov', '*.avi', '*.mkv', '*.webm'):
        for p in sorted(VIDEOS.glob(ext)):
            name = p.stem
            out_dir = OUTPUTS / name
            has_match = (out_dir / 'detections.jsonl').exists()
            has_posture = (out_dir / 'posture' / 'drill_summary.json').exists()
            has_annotations = (out_dir / 'court_annotations.txt').exists()
            thumb_path = out_dir / 'thumb.jpg'
            if not thumb_path.exists():
                _ensure_thumbnail(p, name)
            if has_match or has_posture:
                status = 'analyzed'
            elif has_annotations:
                status = 'court_set'
            else:
                status = 'new'
            videos.append({
                'name': name,
                'filename': p.name,
                'size_mb': round(p.stat().st_size / 1024 / 1024, 1),
                'has_result': has_match,
                'has_annotations': has_annotations,
                'has_match': has_match,
                'has_posture': has_posture,
                'status': status,
                'date': datetime.date.fromtimestamp(p.stat().st_mtime).isoformat(),
                'duration_sec': _video_duration_sec(p),
                'thumb': f'/api/output/{name}/thumb.jpg' if thumb_path.exists() else None,
                'output_dir': str(out_dir) if has_match else None,
            })
    return jsonify(videos)


@app.route('/api/stats')
def api_stats():
    """Dashboard tiles: counts + aggregate scores across all outputs."""
    count = analyzed = rallies = 0
    score_sum = score_n = 0.0
    for ext in ('*.mp4', '*.mov', '*.avi', '*.mkv', '*.webm'):
        for p in VIDEOS.glob(ext):
            count += 1
            out = OUTPUTS / p.stem
            has_match = (out / 'detections.jsonl').exists()
            has_posture = (out / 'posture' / 'drill_summary.json').exists()
            if has_match or has_posture:
                analyzed += 1
            rf = out / 'rally_segments.json'
            if rf.exists():
                try:
                    with open(rf, encoding='utf-8') as f:
                        rallies += len(json.load(f).get('rallies', []))
                except Exception:
                    pass
            tf = out / 'technique_summary.json'
            if tf.exists():
                try:
                    with open(tf, encoding='utf-8') as f:
                        by_type = json.load(f).get('by_type', {})
                    vals = [v['avg_score'] for v in by_type.values()
                            if isinstance(v, dict) and v.get('avg_score') is not None]
                    if vals:
                        score_sum += sum(vals) / len(vals)
                        score_n += 1
                except Exception:
                    pass
    avg = round(score_sum / score_n, 1) if score_n else None
    return jsonify({'videos': count, 'analyzed': analyzed,
                    'rallies': rallies, 'avg_technique_score': avg})


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
    _ensure_thumbnail(save_path, save_path.stem)
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
    analyze_technique = data.get('analyze_technique', True)

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
    if analyze_technique:
        cmd.append('--analyze-technique')

    job_id = video_path.stem
    jobs[job_id] = {
        'status': 'pending',
        'progress': 0,
        'message': '准备启动...',
        'video_name': video_name,
        'save_dir': str(save_dir),
        'stage': 'analyzing',
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
                job['stage'] = 'analyzing'
            time.sleep(1.5)

        proc.wait()

        if proc.returncode == 0:
            job['status'] = 'reencoding'
            job['stage'] = 'encoding'
            job['progress'] = 99
            job['message'] = '正在转码为浏览器兼容格式...'

            # FFmpeg 转码为 H.264（浏览器兼容）
            vs = video_path.stem
            sd = str(save_dir)
            raw_video = os.path.join(sd, f'detect_{vs}.mp4')
            h264_video = os.path.join(sd, f'detect_{vs}_h264.mp4')
            ffmpeg_bin = shutil.which('ffmpeg') or os.path.expanduser('~/.local/bin/ffmpeg')

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
            job['stage'] = 'done'
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
            job['stage'] = 'error'
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
        'stage': job.get('stage'),
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

    # 优先使用标注视频（有分析叠加层），否则回退到原始视频
    analyzed_video = save_dir / f'detect_{video_path.stem}.mp4'
    source_video = analyzed_video if analyzed_video.exists() else video_path

    cmd = [
        _venv_python, str(PROJECT_ROOT / 'clip_video.py'),
        '--video-path', str(source_video),
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
    if _safe_stem(video_name) is None:
        return jsonify({'error': '无效的删除请求'}), 400
    base = (OUTPUTS / video_name).resolve()
    filepath = (OUTPUTS / video_name / subpath).resolve()
    try:
        filepath.relative_to(base)
    except ValueError:
        return jsonify({'error': '无效的删除请求'}), 400
    if filepath.is_dir():
        shutil.rmtree(str(filepath))
    elif filepath.exists():
        filepath.unlink()
    return jsonify({'ok': True})


@app.route('/api/technique/<video_name>')
def api_technique(video_name):
    """Return technique summary + per-stroke reports for a video."""
    out_dir = OUTPUTS / video_name
    summary_path = out_dir / 'technique_summary.json'
    if not summary_path.exists():
        return jsonify({'error': '还没有技术分析数据，请先用 --analyze-technique 运行分析'}), 404

    try:
        with open(summary_path, encoding='utf-8') as f:
            summary = json.load(f)

        strokes = []
        strokes_path = out_dir / 'strokes.jsonl'
        if strokes_path.exists():
            with open(strokes_path, encoding='utf-8') as f:
                for line in f:
                    line = line.strip()
                    if line:
                        strokes.append(json.loads(line))
    except Exception as e:
        return jsonify({'error': 'failed to read report'}), 500

    return jsonify({'summary': summary, 'strokes': strokes})


def _load_or_make_training_plan(video_name, weeks=4, force=False):
    out_dir = OUTPUTS / video_name
    plan_path = out_dir / 'training_plan.json'
    summary_path = out_dir / 'technique_summary.json'

    if plan_path.exists() and not force:
        try:
            with open(plan_path, encoding='utf-8') as f:
                return json.load(f), 200
        except Exception as e:
            return {'error': str(e)}, 500

    if not summary_path.exists():
        return {'error': '没有技术分析数据，无法生成训练计划'}, 404

    try:
        with open(summary_path, encoding='utf-8') as f:
            summary = json.load(f)
        plan = generate_plan(summary, weeks=weeks)
        write_json(str(plan_path), plan)
    except Exception as e:
        return {'error': str(e)}, 500

    return plan, 200


@app.route('/api/training-plan/<video_name>', methods=['GET'])
def api_training_plan(video_name):
    plan, status = _load_or_make_training_plan(video_name)
    return jsonify(plan), status


@app.route('/api/training-plan/<video_name>', methods=['POST'])
def api_training_plan_regenerate(video_name):
    data = request.json or {}
    try:
        weeks = int(data.get('weeks', 4))
    except (TypeError, ValueError):
        return jsonify({'error': 'weeks 必须是整数'}), 400
    plan, status = _load_or_make_training_plan(video_name, weeks=weeks, force=True)
    return jsonify(plan), status


@app.route('/api/posture/<video_name>')
def api_posture(video_name):
    """Return drill summary + per-rep reports for a posture-drill video."""
    out_dir = OUTPUTS / video_name / 'posture'
    summary_path = out_dir / 'drill_summary.json'
    if not summary_path.exists():
        return jsonify({'error': '还没有姿态训练分析数据，请先运行姿态分析'}), 404
    try:
        with open(summary_path, encoding='utf-8') as f:
            summary = json.load(f)
        reps = []
        reps_path = out_dir / 'drill_reps.jsonl'
        if reps_path.exists():
            with open(reps_path, encoding='utf-8') as f:
                for line in f:
                    line = line.strip()
                    if line:
                        reps.append(json.loads(line))
        return jsonify({'summary': summary, 'reps': reps})
    except Exception as e:
        return jsonify({'error': 'failed to read report'}), 500


@app.route('/api/posture-rep-clip/<video_name>', methods=['POST'])
def api_posture_rep_clip(video_name):
    """On-demand ffmpeg crop of a single rep window from the annotated posture video."""
    data = request.get_json(silent=True) or {}
    rep_id = data.get('rep_id')
    if rep_id is None:
        return jsonify({'error': '需要 rep_id'}), 400
    out_dir = OUTPUTS / video_name / 'posture'
    reps_path = out_dir / 'drill_reps.jsonl'
    if not reps_path.exists():
        return jsonify({'error': '没有 rep 数据'}), 404
    rep = None
    try:
        with open(reps_path, encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                r = json.loads(line)
                if str(r.get('rep_id')) == str(rep_id):
                    rep = r
                    break
    except Exception:
        return jsonify({'error': 'rep 数据读取失败'}), 500
    if rep is None:
        return jsonify({'error': 'rep 不存在'}), 404
    video_file = out_dir / ('detect_' + video_name + '.mp4')
    if not video_file.exists():
        return jsonify({'error': '没有标注视频'}), 404
    fps = 30.0
    meta_path = out_dir / 'metadata.json'
    if meta_path.exists():
        try:
            with open(meta_path, encoding='utf-8') as f:
                fps = (json.load(f).get('video', {}) or {}).get('fps') or 30.0
        except Exception:
            fps = 30.0
    start, duration = _rep_clip_window(rep.get('contact_frame', 0), fps)
    clip_dir = out_dir / 'rep_clips'
    clip_dir.mkdir(exist_ok=True)
    out_file = clip_dir / ('rep_' + str(rep_id) + '.mp4')
    ffmpeg_bin = shutil.which('ffmpeg') or 'ffmpeg'
    try:
        subprocess.run([ffmpeg_bin, '-y', '-ss', str(start), '-i', str(video_file),
                        '-t', str(duration), '-c:v', 'libx264', '-preset', 'fast',
                        '-crf', '23', '-movflags', '+faststart', str(out_file)],
                       check=True, capture_output=True, timeout=120)
    except Exception as e:
        return jsonify({'error': 'ffmpeg 失败: ' + str(e)}), 500
    return jsonify({'ok': True,
                    'url': '/api/output/' + video_name + '/posture/rep_clips/rep_' + str(rep_id) + '.mp4'})


@app.route('/api/delete-preview/<video_name>')
def api_delete_preview(video_name):
    """What a delete scope would remove: disjoint groups with file counts + sizes."""
    scope = request.args.get('scope', '')
    stem = _safe_stem(video_name)
    if stem is None or scope not in _DELETE_SCOPES:
        return jsonify({'error': '无效的删除请求'}), 400
    rows = []
    total_files = 0
    total_size = 0.0
    for key, paths in _delete_groups(stem, scope):
        files, size_mb = _paths_stats(paths)
        if files:
            rows.append({'key': key, 'files': files, 'size_mb': size_mb})
            total_files += files
            total_size += size_mb
    return jsonify({'ok': True, 'scope': scope, 'groups': rows,
                    'total_files': total_files, 'total_size_mb': round(total_size, 1)})


@app.route('/api/delete/<video_name>', methods=['POST'])
def api_delete(video_name):
    """Permanently delete a scope's files. 409 while an analysis job runs on the video."""
    data = request.get_json(silent=True) or {}
    scope = data.get('scope', '')
    stem = _safe_stem(video_name)
    if stem is None or scope not in _DELETE_SCOPES:
        return jsonify({'error': '无效的删除请求'}), 400
    if _running_job_for(stem):
        return jsonify({'error': '该视频正在分析中，请等待完成后再删除'}), 409
    deleted = 0
    groups = sorted(_delete_groups(stem, scope), key=lambda g: g[0] == 'source')
    try:
        for _key, paths in groups:
            for p in paths:
                if p.is_dir():
                    deleted += sum(1 for f in p.rglob('*') if f.is_file())
                    shutil.rmtree(str(p))
                elif p.is_file():
                    p.unlink()
                    deleted += 1
        # For scope "all", also delete the parent outputs directory
        if scope == 'all':
            out_dir = OUTPUTS / stem
            if out_dir.is_dir() and not list(out_dir.iterdir()):
                shutil.rmtree(str(out_dir))
            for stale_id in (stem, 'posture_' + stem):
                job = jobs.get(stale_id)
                if job and job.get('status') not in ('pending', 'running', 'reencoding'):
                    jobs.pop(stale_id, None)
    except Exception:
        return jsonify({'error': '删除失败'}), 500
    return jsonify({'ok': True, 'scope': scope, 'deleted_files': deleted})


def _load_or_make_posture_plan(video_name, weeks=4, force=False):
    out_dir = OUTPUTS / video_name / 'posture'
    plan_path = out_dir / 'training_plan.json'
    summary_path = out_dir / 'drill_summary.json'
    if plan_path.exists() and not force:
        try:
            with open(plan_path, encoding='utf-8') as f:
                return json.load(f), 200
        except Exception as e:
            return {'error': str(e)}, 500
    if not summary_path.exists():
        return {'error': '没有姿态分析数据，无法生成训练计划'}, 404
    try:
        with open(summary_path, encoding='utf-8') as f:
            summary = json.load(f)
        plan = generate_plan(summary, weeks=weeks)
        write_json(str(plan_path), plan)
        return plan, 200
    except Exception as e:
        return {'error': str(e)}, 500


@app.route('/api/posture-plan/<video_name>', methods=['GET'])
def api_posture_plan(video_name):
    plan, status = _load_or_make_posture_plan(video_name)
    return jsonify(plan), status


@app.route('/api/posture-plan/<video_name>', methods=['POST'])
def api_posture_plan_regenerate(video_name):
    data = request.json or {}
    try:
        weeks = int(data.get('weeks', 4))
    except (TypeError, ValueError):
        return jsonify({'error': 'weeks 必须是整数'}), 400
    plan, status = _load_or_make_posture_plan(video_name, weeks=weeks, force=True)
    return jsonify(plan), status


@app.route('/api/posture/analyze', methods=['POST'])
def api_posture_analyze():
    data = request.json or {}
    video_name = data.get('video')
    stroke_type = data.get('stroke_type')
    dominant = data.get('dominant_hand', 'right')
    pose_family = data.get('pose_family', 'yolo-pose')
    report_llm = data.get('report_llm', 'off')
    if pose_family not in ('yolo-pose', 'rtmpose', 'rtmo'):
        return jsonify({'error': 'invalid pose_family'}), 400
    if not video_name or stroke_type not in ('high_clear', 'smash', 'drop_shot', 'serve'):
        return jsonify({'error': '需要 video 和有效的 stroke_type'}), 400

    video_path = VIDEOS / video_name
    if not video_path.exists():
        return jsonify({'error': '视频不存在'}), 404

    save_dir = OUTPUTS / video_path.stem / 'posture'
    save_dir.mkdir(parents=True, exist_ok=True)

    cmd = [
        _venv_python, str(PROJECT_ROOT / 'main_posture.py'),
        '--video-path', str(video_path),
        '--stroke-type', stroke_type,
        '--dominant-hand', dominant,
        '--pose-family', pose_family,
        '--output-dir', str(save_dir),
        '--display', 'false',
        '--report-llm', report_llm,
    ]
    job_id = 'posture_' + video_path.stem
    jobs[job_id] = {'status': 'running', 'progress': 0, 'message': '姿态分析中...',
                    'video_name': video_path.stem, 'save_dir': str(save_dir)}
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            text=True, cwd=str(PROJECT_ROOT), env=os.environ.copy())
    jobs[job_id]['proc'] = proc

    import threading

    def track():
        job = jobs[job_id]
        job['stage'] = 'loading'
        for line in proc.stdout:
            parsed = _parse_progress_line(line)
            if parsed:
                job['progress'], job['stage'] = parsed[0], parsed[1]
        proc.wait()
        if proc.returncode == 0:
            job['stage'] = 'encoding'
            job['progress'] = max(job.get('progress', 0), 96)
            job['message'] = '完成!'
            vs = video_path.stem
            sd = str(save_dir)
            raw = os.path.join(sd, 'detect_' + vs + '.mp4')
            h264 = os.path.join(sd, 'detect_' + vs + '_h264.mp4')
            ffmpeg_bin = shutil.which('ffmpeg') or 'ffmpeg'
            try:
                subprocess.run([ffmpeg_bin, '-y', '-i', raw, '-c:v', 'libx264',
                                '-preset', 'fast', '-crf', '23', '-movflags', '+faststart', h264],
                               check=True, capture_output=True, timeout=600)
                if os.path.exists(h264) and os.path.getsize(h264) > 0:
                    os.replace(h264, raw)
            except Exception as e:
                print('posture ffmpeg re-encode failed: ' + str(e))
            job['status'] = 'completed'
            job['progress'] = 100
            job['stage'] = 'done'
            job['result'] = {'video_name': vs,
                             'output_video': '/api/output/' + vs + '/posture/detect_' + vs + '.mp4'}
        else:
            job['status'] = 'error'
            job['stage'] = 'error'
            job['message'] = '姿态分析失败 (exit=' + str(proc.returncode) + ')'

    threading.Thread(target=track, daemon=True).start()
    return jsonify({'ok': True, 'job_id': job_id})


@app.route('/api/posture-analyze-status/<job_id>')
def api_posture_analyze_status(job_id):
    job = jobs.get(job_id)
    if not job:
        return jsonify({'status': 'not_found'}), 404
    return jsonify({'status': job.get('status'), 'progress': job.get('progress', 0),
                    'message': job.get('message', ''), 'result': job.get('result'),
                    'stage': job.get('stage')})


_REPORT_LANGS = ("en", "zh-Hant", "zh-Hans")


@app.route('/api/posture-report/<video_name>')
def api_posture_report(video_name):
    lang = request.args.get('lang', 'en')
    if lang not in _REPORT_LANGS:
        return jsonify({'error': 'invalid lang'}), 400
    path = OUTPUTS / video_name / 'posture' / ('coach_report_' + lang + '.json')
    if not path.exists():
        return jsonify({'error': '还没有教练报告，请先运行姿态分析'}), 404
    try:
        with open(path, encoding='utf-8') as f:
            return jsonify(json.load(f))
    except Exception as e:
        return jsonify({'error': 'failed to read report'}), 500


# ═══════════════════════════════════════════════════════════════════════════
# Static page
# ═══════════════════════════════════════════════════════════════════════════

@app.route('/')
def index():
    return (PROJECT_ROOT / 'web_ui.html').read_text(encoding='utf-8')


@app.route('/kestrel')
def kestrel():
    return (PROJECT_ROOT / 'kestrel.html').read_text(encoding='utf-8')


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
