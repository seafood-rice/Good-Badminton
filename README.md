# Good-Badminton: AI 羽毛球鹰眼系统 🏸

<div align="center">

[![GitHub stars](https://img.shields.io/github/stars/qwpyyx/Good-Badminton?style=social)](https://github.com/qwpyyx/Good-Badminton/stargazers)
[![GitHub license](https://img.shields.io/github/license/qwpyyx/Good-Badminton)](https://github.com/qwpyyx/Good-Badminton/blob/main/LICENSE)

**基于计算机视觉的羽毛球比赛视频分析工具 | 一键 Web 界面，告别命令行**

</div>

> 🛠 Forked from [yo-WASSUP/Good-Badminton](https://github.com/yo-WASSUP/Good-Badminton) (Apache 2.0)，在此基础上做了大量改进。

## 🆕 本 Fork 改进

| 改进 | 说明 |
|------|------|
| 🌐 **Web 前端** | 一键启动 Flask 界面，浏览器里完成所有操作，不需要记命令行参数 |
| 🎨 **自适应球场检测** | 自动识别地板颜色（绿/红/橙/蓝），不再只支持绿色球场 |
| 📐 **手动标注 + 实时预览** | 点击四个角点即可标注球场，提交后立即看到绿色四边形反馈 |
| 🎬 **H.264 视频输出** | 输出视频可直接在浏览器/手机上播放，无需额外转码 |
| 📤 **拖拽上传** | 直接拖视频到页面即可上传分析 |
| 📊 **实时进度** | 进度条实时显示分析进度，不用盯着终端 |
| ✂️ **回合智能剪辑** | 自动检测回合，一键生成集锦视频或单独回合片段，去除捡球等冗余 |

## 🎬 效果预览

![分析结果预览](assets/demo.gif)

## 🚀 快速开始

### 1. 安装

```bash
# 克隆仓库
git clone https://github.com/qwpyyx/Good-Badminton.git
cd Good-Badminton

# 创建虚拟环境并安装依赖
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# macOS 安装 FFmpeg（如果还没有）
# 下载: https://evermeet.cx/ffmpeg/
# 解压放到 ~/.local/bin/ 并加入 PATH
```

### 2. 下载模型权重

从 [yo-WASSUP/Good-Badminton Releases](https://github.com/yo-WASSUP/Good-Badminton/releases/latest) 下载：
- `yolo11s-ball.pt` → 放到 `weights/`
- `yolox_nano_8xb8-300e_humanart-40f6f0d0.onnx` → 放到 `weights/`

首次运行 YOLO pose 模型时会自动下载 `yolo11n-pose.pt`。

### 3. 启动 Web 界面（推荐 ✨）

```bash
source .venv/bin/activate
python app.py
```

打开 http://127.0.0.1:5050 即可使用。

### 4. 命令行方式（高级用户）

```bash
python main.py --video-path videos/demo.mp4
```

## 🌐 Web 界面使用指南

### 自动模式

1. 浏览器打开 http://127.0.0.1:5050
2. 点击选中视频（或拖拽上传新视频）
3. 点击 **「🔍 自动检测球场」** — 程序自动识别地板颜色和球场边界
4. 查看检测结果，如果边线准确，直接点 **「▶ 开始分析」**
5. 等待进度条完成，查看标注视频、热力图和散点图

### 手动修正模式

如果自动检测的球场边线不准确：

1. 点击 **「✏️ 手动修正」**
2. 在图片上按顺序点击球场四个角点：
   - **1️⃣ 左上角** (红点)
   - **2️⃣ 右上角** (绿点)
   - **3️⃣ 右下角** (蓝点)
   - **4️⃣ 左下角** (黄点)
3. 点击 **「✅ 确认提交」** — 会显示绿色四边形预览
4. 确认边线正确后，点击 **「▶ 开始分析」**

> 💡 角点要选 **球场边界白线的交点**，不是看台或广告牌。

### 回合剪辑模式

分析完成后，结果面板会显示检测到的回合数。可以一键生成剪辑：

- **集锦视频**：所有回合拼接为一个视频，自动去除捡球和回放
- **单独回合**：每个回合输出为独立 mp4 文件
- 剪辑前后保留 1.5 秒缓冲，确保动作完整

> 💡 长视频建议先用剪辑模式定位回合，再对关键回合做精确分析。

## ✨ 功能

- **球员姿态检测** — 支持 RTMPose、RTMO 和 YOLO Pose，识别人体关键点
- **羽毛球检测** — YOLO 模型检测羽毛球位置并绘制轨迹
- **球场坐标映射** — 自动/手动标注球场，将图像坐标映射到标准球场坐标
- **自适应球场颜色** — 自动识别绿/红/橙/蓝等不同颜色的球场地板
- **球员追踪** — 分别追踪上下半场球员，记录移动轨迹
- **回合检测** — 根据球场视图自动判断回合开始和结束
- **运动统计** — 移动距离、速度、最大速度、回合数量
- **可视化输出** — 带骨架、轨迹、统计覆盖层的标注视频 (H.264)
- **位置图表** — 热力图和散点图
- **中英文** — 可视化文字可切换
- **击球技术分析** — 检测击球类型，分析关键关节角度，生成生物力学评分和改进建议
- **智能训练计划** — 针对检测到的薄弱环节生成进阶训练计划，包括球场和居家训练

## 🎯 击球技术分析

启用技术分析后，系统会自动识别击球类型，分析关键关节角度，生成生物力学评分和改进建议，并生成个性化训练计划。

### 启用技术分析

在命令行添加 `--analyze-technique` 标志：

```bash
python main.py --video-path videos/demo.mp4 --analyze-technique
```

#### 可选参数

- `--racket-model weights/yolo11s-racket.pt` — YOLO 球拍检测模型路径。**可选**：如果不提供或文件不存在，系统会自动使用运动学降级方案，从肘部和腕部关键点推断球拍位置，技术分析功能仍可正常工作（精度略低）。
- `--dominant-hand right|left` — 运动员持拍手（默认 `right`）。

### 输出文件

启用技术分析后，输出目录 `outputs/<视频名>/` 中会生成：

| 文件 | 说明 |
|------|------|
| `strokes.jsonl` | 每一击的生物力学分析报告（击球类型、综合评分、各关节角度评分、薄弱环节和改进建议） |
| `technique_summary.json` | 比赛级汇总：各类型击球数量、平均评分、常见薄弱环节 |
| `training_plan.json` | 当通过 Web UI 请求时生成的进阶训练计划（目标定制化，分为球场和居家训练） |

### Web UI 技术分析面板

分析完成后，结果页面会显示 **技术分析** 面板，包含：

- **击球列表** — 所有识别的击球及其综合评分
- **击球详情** — 关键关节角度与理想范围对比，改进建议
- **比赛汇总** — 击球类型统计、平均评分、常见薄弱环节
- **训练计划** — 生成针对检测薄弱环节的多周进阶计划，可切换球场/居家模式，支持重新生成

### 支持的击球类型

- 高远球（High Clear）
- 杀球（Smash）
- 吊球（Drop Shot）
- 发球（Serve）

### 注意事项

- 球拍检测是**可选**的。如果未提供 `--racket-model` 或模型文件不存在，系统会自动使用运动学推断（基于肘部、腕部关键点推断球拍位置）。
- 生物力学参考范围（`badminton_analysis/analysis/reference_ranges.py`）是第一版的参考值，可根据训练需求调整。
- 生物力学评分基于关键关节角度，包括肩、肘、腕、髋、膝、踝等。

## 🧍 姿态训练 — 重复动作钻练

除了比赛分析，系统支持**单人姿态钻练模式**，用于重复练习同一种击球动作并获得实时反馈。

### 什么是姿态训练

- **免球场钻练** — 无需标注球场，适用于日常训练和居家练习
- **单一击球类型** — 选择一种击球（高远球、杀球、吊球、发球），重复多次
- **侧面视角** — 从**侧面/正侧方** 拍摄，便于捕捉关键关节角度
- **自动分段** — 系统根据挥拍动作的腕速峰值自动识别每一个重复动作，无需手动标记
- **可选穿梭** — 如果视频中有羽毛球，系统会用它来精化击球接触时刻；没有球也支持（影子练习）

### 启用姿态训练

在命令行使用 `main_posture.py`：

```bash
python main_posture.py --video-path videos/drill.mov --stroke-type high_clear --dominant-hand right
```

#### 必需参数

- `--video-path` — 输入视频路径
- `--stroke-type` — 击球类型：`high_clear`（高远球）/ `smash`（杀球）/ `drop_shot`（吊球）/ `serve`（发球）

#### 可选参数

- `--dominant-hand` — 持拍手：`right`（右手）或 `left`（左手），默认 `right`
- `--output-dir` — 输出目录，默认 `outputs/<视频名>/posture`
- `--ball-model` — 羽毛球检测模型路径，可选
- `--display` — 显示 OpenCV 窗口，默认 `false`

### 输出文件

姿态训练完成后，输出目录 `outputs/<视频名>/posture/` 中会生成：

| 文件 | 说明 |
|------|------|
| `detect_<视频名>.mp4` | 标注视频，实时显示关节角度叠加层 |
| `drill_reps.jsonl` | 每一个重复动作的生物力学分析报告（各关节角度评分、薄弱环节、改进建议） |
| `drill_summary.json` | 钻练汇总：重复次数、平均/最高/最低评分、一致性（重复动作评分的标准差；越低越稳定）、常见薄弱环节 |
| `training_plan.json` | 当通过 Web UI 请求时生成的进阶训练计划（可选） |

### Web UI 姿态训练面板

从顶部模式开关选择 **🧍 姿态训练**，进入钻练流程：

1. **选择击球类型和持拍手** — 高远球、杀球、吊球或发球，以及左手/右手
2. **上传视频并分析** — 无需标注球场，系统自动识别重复动作
3. **查看钻练成绩** — 
   - **重复列表** — 每一个识别的重复动作及其评分（徽章显示）
   - **重复详情** — 点击列表项，查看该重复动作的关键关节角度、与理想范围对比、改进建议
   - **钻练汇总** — 总体评分、一致性、常见薄弱环节
4. **生成训练计划** — 支持 **球场/居家** 切换，可重新生成

### 支持的击球类型

与比赛模式相同：高远球、杀球、吊球、发球。

### 注意事项

- **侧面视角是本版本的重点支持视角**，其他相机角度（正面、背后等）为后续工作
- 生物力学参考范围与比赛模式共用，基于关键关节角度（肩、肘、腕、髋、膝、踝等）

## 📋 系统要求

- Python 3.8+
- FFmpeg（加入 PATH）
- 模型权重从 [Releases](https://github.com/yo-WASSUP/Good-Badminton/releases/latest) 下载

**推荐配置：**
- GPU 6GB+ 显存；或 Apple Silicon Mac (MPS)
- 16GB+ 内存
- CPU 也能跑，但 4K 视频会较慢（约 10-20 分钟/20 秒视频）

## 📊 输出结果

默认输出到 `outputs/<视频文件名>/`：

```
outputs/demo/
├── detect_demo.mp4          # 标注视频 (H.264, 浏览器可播)
├── detections.jsonl          # 逐帧检测数据
├── auto_court_preview.png    # 球场检测预览
├── manual_court_preview.png  # 手动标注预览
├── court_annotations.txt     # 球场坐标缓存
├── metadata.json             # 运行元数据
├── rally_segments.json       # 回合分段数据
├── clips/                    # 回合剪辑输出
│   ├── highlights.mp4        # 集锦视频（所有回合合并）
│   └── rally_001.mp4         # 单个回合片段
└── position_visualizations/
    ├── heatmaps/             # 热力图
    └── scatter_plots/        # 散点图
```

## 🧩 项目结构

```text
app.py               # 🌐 Web 前端入口（推荐）
main.py              # 命令行入口
court_detect.py      # 无头球场检测脚本
clip_video.py        # ✂️ 回合视频剪辑脚本
web_ui.html          # 前端页面
badminton_analysis/
├── system.py        # 分析主流程
├── court/           # 球场检测与坐标映射 (自适应颜色)
├── data/            # JSON/JSONL 输出
├── detection/       # 羽毛球 & 姿态检测
├── media/           # 视频 & 音频处理 (H.264)
├── tracking/        # 球员追踪
├── stroke/          # 击球检测与分类
├── analysis/        # 生物力学分析与评分
├── training/        # 训练计划生成
└── visualization/   # 叠加层、统计图、位置图
```

## 🔧 命令行参数（高级）

```text
--video-path              输入视频路径
--template-path            球场模板图（可选，会自动提取）
--output-dir               输出目录，默认 outputs/<视频名>
--pose-family              rtmpose / rtmo / yolo-pose
--pose-mode                lightweight / balanced / performance
--language                 zh / en
--display false            关闭 OpenCV 窗口（服务器模式）
--analyze-technique        启用击球技术分析（默认关闭）
--racket-model             球拍检测模型路径，可选；不提供时使用运动学推断
--dominant-hand            持拍手：right 或 left（默认 right）
```

## 🙏 致谢与许可

本项目 Fork 自 [yo-WASSUP/Good-Badminton](https://github.com/yo-WASSUP/Good-Badminton)。

- RTMPose、RTMO 和 OpenMMLab 生态
- [Ultralytics YOLO](https://github.com/ultralytics/ultralytics)
- [Tau-J/rtmlib](https://github.com/Tau-J/rtmlib)
- [yastrebksv/TrackNet](https://github.com/yastrebksv/TrackNet)

**许可证**：
- 原始代码：[Apache License 2.0](https://github.com/yo-WASSUP/Good-Badminton/blob/main/LICENSE)
- 本 Fork 新增/修改代码：[CC BY-NC 4.0](https://creativecommons.org/licenses/by-nc/4.0/) — **禁止商用**
