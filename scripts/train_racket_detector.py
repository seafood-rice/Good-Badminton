"""Fine-tune a YOLO racket detector on RacketDB and install the weights.

Dataset: https://github.com/muhabdulhaq/racketdb (full images on Hugging Face:
https://huggingface.co/datasets/muhabdulhaq/racketdb). Download it to data/racketdb
first, e.g.:

    ./.venv/Scripts/pip.exe install -U huggingface_hub
    ./.venv/Scripts/python.exe -c "from huggingface_hub import snapshot_download; \
snapshot_download('muhabdulhaq/racketdb', repo_type='dataset', local_dir='data/racketdb')"

License note: the RacketDB repository states no license. Weights trained on it are for
local/personal use; do not commit or redistribute them until the license is clarified.
"""
import argparse
import shutil
import sys
from pathlib import Path

_DOWNLOAD_HELP = (
    "RacketDB not found or empty at %s.\n"
    "Download it first (see the docstring at the top of this script), then re-run.\n"
    "Expected either a data.yaml at the root, or split dirs like train/images,\n"
    "valid/images (Roboflow/YOLOv8 export) or images/train, images/val (CVAT export)."
)


def find_dataset_yaml(root):
    for name in ("data.yaml", "dataset.yaml"):
        p = Path(root) / name
        if p.is_file():
            return p
    return None


def discover_splits(root):
    root = Path(root)
    out = {"train": None, "val": None, "test": None}
    aliases = {"train": ("train",), "val": ("valid", "val"), "test": ("test",)}
    for key, names in aliases.items():
        for name in names:
            a = root / name / "images"          # Roboflow-style
            b = root / "images" / name          # CVAT-style
            if a.is_dir():
                out[key] = a
                break
            if b.is_dir():
                out[key] = b
                break
    if out["train"] is None:
        return None
    return out


def build_dataset_yaml(root, out_path):
    root = Path(root)
    out_path = Path(out_path)
    existing = find_dataset_yaml(root)
    if existing is not None:
        lines = []
        wrote_path = False
        for line in existing.read_text(encoding="utf-8").splitlines():
            if line.strip().startswith("path:"):
                lines.append("path: " + str(root.resolve()))
                wrote_path = True
            else:
                lines.append(line)
        if not wrote_path:
            lines.insert(0, "path: " + str(root.resolve()))
        out_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return out_path
    splits = discover_splits(root)
    if splits is None:
        raise SystemExit(_DOWNLOAD_HELP % root)
    lines = ["path: " + str(root.resolve()),
             "train: " + str(splits["train"].relative_to(root))]
    lines.append("val: " + str((splits["val"] or splits["train"]).relative_to(root)))
    if splits["test"] is not None:
        lines.append("test: " + str(splits["test"].relative_to(root)))
    lines += ["nc: 1", "names: ['racket']"]
    out_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return out_path


def main():
    ap = argparse.ArgumentParser(description="Train the RacketDB racket detector")
    ap.add_argument("--data", default="data/racketdb")
    ap.add_argument("--model", default="yolo11n.pt")
    ap.add_argument("--epochs", type=int, default=60)
    ap.add_argument("--imgsz", type=int, default=640)
    ap.add_argument("--batch", type=int, default=-1)
    ap.add_argument("--device", default="0")
    ap.add_argument("--out", default="weights/yolo11n-racket.pt")
    ap.add_argument("--smoke", action="store_true",
                    help="1 epoch on 2%% of the data at imgsz 320 — script sanity only")
    args = ap.parse_args()

    root = Path(args.data)
    license_file = next((p for p in (root / "LICENSE", root / "LICENSE.md",
                                     root / "LICENSE.txt") if p.is_file()), None)
    if license_file is not None:
        print("RacketDB license file found: " + str(license_file))
    else:
        print("NOTE: RacketDB states no license. Trained weights are for local use only; "
              "do not commit or redistribute them.")

    yaml_path = build_dataset_yaml(root, root / "racketdb.ultralytics.yaml")
    print("Dataset yaml: " + str(yaml_path))

    from ultralytics import YOLO
    model = YOLO(args.model)
    train_kwargs = dict(data=str(yaml_path), epochs=args.epochs, imgsz=args.imgsz,
                        batch=args.batch, device=args.device, seed=0,
                        project="runs", name="racketdb")
    if args.smoke:
        train_kwargs.update(epochs=1, fraction=0.02, imgsz=320)
        print("SMOKE MODE: 1 epoch, 2% of data, imgsz 320")
    results = model.train(**train_kwargs)

    metrics = model.val(data=str(yaml_path), device=args.device)
    print("val mAP50:    %.4f" % metrics.box.map50)
    print("val mAP50-95: %.4f" % metrics.box.map)

    best = Path(results.save_dir) / "weights" / "best.pt"
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(best, out)
    print("Installed weights: " + str(out))


if __name__ == "__main__":
    main()
