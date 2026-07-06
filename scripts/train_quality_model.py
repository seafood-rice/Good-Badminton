"""Train a TCN regressor for badminton shot quality prediction.

Loads prepared dataset (manifest + .npy tensors), splits by player,
trains TCN on mean coach score, exports TorchScript weights.
"""
import argparse
import copy
import json
from pathlib import Path

BUCKETS = ("novice", "intermediate", "expert")


def bucket_of(score):
    """Classify score into one of three buckets: novice, intermediate, expert."""
    if score < 3.0:
        return "novice"
    elif score < 6.0:
        return "intermediate"
    else:
        return "expert"


def player_split(rows, val_fraction=0.2, seed=0):
    """Split rows by player with no leakage: train and val have disjoint player sets.

    Returns (train_rows, val_rows) lists. Raises SystemExit when fewer than 2
    unique players are present (a player-level split is meaningless below that);
    `n_val` is capped so at least one player always remains for training.
    """
    import random

    # Get unique players
    players = sorted({r["player"] for r in rows})
    if len(players) < 2:
        raise SystemExit("player-level split needs >=2 players; got %d" % len(players))

    # Shuffle players deterministically
    rng = random.Random(seed)
    rng.shuffle(players)

    # Split players (always leave >=1 player for train)
    n_val = max(1, int(round(len(players) * val_fraction)))
    n_val = min(n_val, len(players) - 1)
    val_players = set(players[:n_val])

    # Partition rows
    train = [r for r in rows if r["player"] not in val_players]
    val = [r for r in rows if r["player"] in val_players]

    return train, val


def main():
    ap = argparse.ArgumentParser(
        description="Train quality model (TCN regressor) on coach ratings"
    )
    ap.add_argument(
        "--manifest",
        default="data/multisense/manifest.jsonl",
        help="Manifest file with swing metadata",
    )
    ap.add_argument(
        "--data-dir",
        default="data/multisense",
        help="Root directory for prepared tensors",
    )
    ap.add_argument(
        "--val-players",
        type=float,
        default=0.2,
        help="Fraction of players to reserve for validation",
    )
    ap.add_argument(
        "--epochs",
        type=int,
        default=40,
        help="Number of training epochs",
    )
    ap.add_argument(
        "--batch-size",
        type=int,
        default=64,
        help="Batch size",
    )
    ap.add_argument(
        "--lr",
        type=float,
        default=1e-3,
        help="Learning rate",
    )
    ap.add_argument(
        "--out",
        default="weights/quality-high_clear.pt",
        help="Output TorchScript path",
    )
    ap.add_argument(
        "--seed",
        type=int,
        default=0,
        help="Random seed",
    )
    ap.add_argument(
        "--device",
        default="cuda-if-available",
        help="Torch device: 'cuda-if-available' (default), 'cpu', 'cuda', etc.",
    )
    ap.add_argument(
        "--smoke",
        action="store_true",
        help="Smoke-test mode: 2 epochs, <=200 swings",
    )
    args = ap.parse_args()

    # Heavy imports inside main()
    import numpy as np
    import torch
    import torch.nn as nn
    from torch.utils.data import DataLoader, TensorDataset

    # Set random seed
    torch.manual_seed(args.seed)
    np.random.seed(args.seed)

    # Load manifest
    manifest_path = Path(args.manifest)
    rows = []
    if manifest_path.is_file():
        with open(manifest_path, "r", encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    rows.append(json.loads(line))
    else:
        print(f"Manifest not found: {manifest_path}")
        print("(This is expected if prepare_quality_dataset has not been run)")
        return

    print(f"Loaded {len(rows)} swing records from manifest")

    # Adjust for smoke mode
    epochs = 2 if args.smoke else args.epochs
    if args.smoke and len(rows) > 200:
        rows = rows[:200]
        print(f"Smoke mode: limited to {len(rows)} rows")

    if len(rows) == 0:
        print("No data to train on")
        return

    # Split by player
    train_rows, val_rows = player_split(rows, val_fraction=args.val_players, seed=args.seed)
    print(f"Train: {len(train_rows)} swings, Val: {len(val_rows)} swings")

    # Load tensors
    data_dir = Path(args.data_dir)
    train_tensors = []
    train_labels = []
    val_tensors = []
    val_labels = []

    for row in train_rows:
        tensor_path = data_dir / row["tensor"]
        if tensor_path.is_file():
            try:
                tensor = np.load(tensor_path)
                train_tensors.append(tensor)
                train_labels.append(row["label"])
            except Exception as e:
                print(f"Failed to load {tensor_path}: {e}")

    for row in val_rows:
        tensor_path = data_dir / row["tensor"]
        if tensor_path.is_file():
            try:
                tensor = np.load(tensor_path)
                val_tensors.append(tensor)
                val_labels.append(row["label"])
            except Exception as e:
                print(f"Failed to load {tensor_path}: {e}")

    if len(train_tensors) == 0:
        print("No training tensors loaded")
        return

    # Convert to torch tensors
    X_train = torch.from_numpy(np.stack(train_tensors)).float()  # (N, 64, 34)
    y_train = torch.from_numpy(np.array(train_labels)).float()   # (N,)
    X_val = torch.from_numpy(np.stack(val_tensors)).float() if val_tensors else X_train[:0]
    y_val = torch.from_numpy(np.array(val_labels)).float() if val_labels else y_train[:0]

    print(f"Loaded {X_train.shape[0]} train, {X_val.shape[0]} val tensors")

    # Define model
    class TCNRegressor(nn.Module):
        def __init__(self, c_in=34, hidden=64):
            super().__init__()
            self.net = nn.Sequential(
                nn.Conv1d(c_in, hidden, 5, padding=2),
                nn.ReLU(),
                nn.Conv1d(hidden, hidden * 2, 5, padding=2, stride=2),
                nn.ReLU(),
                nn.Conv1d(hidden * 2, hidden * 2, 5, padding=2, stride=2),
                nn.ReLU(),
                nn.AdaptiveAvgPool1d(1),
            )
            self.head = nn.Sequential(
                nn.Flatten(),
                nn.Linear(hidden * 2, 64),
                nn.ReLU(),
                nn.Linear(64, 1),
            )

        def forward(self, x):  # x: (B, T, 34)
            return self.head(self.net(x.transpose(1, 2)))

    model = TCNRegressor(c_in=34, hidden=64)

    # Training setup
    if args.device == "cuda-if-available":
        device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    else:
        device = torch.device(args.device)
    model.to(device)
    optimizer = torch.optim.Adam(model.parameters(), lr=args.lr)
    criterion = nn.MSELoss()

    train_dataset = TensorDataset(X_train, y_train)
    train_loader = DataLoader(train_dataset, batch_size=args.batch_size, shuffle=True)

    val_dataset = TensorDataset(X_val, y_val) if len(X_val) > 0 else None
    val_loader = (
        DataLoader(val_dataset, batch_size=args.batch_size, shuffle=False)
        if val_dataset
        else None
    )

    # Compute baseline (predict mean)
    train_mean = y_train.mean().item()
    baseline_mae = torch.abs(y_train - train_mean).mean().item()
    print(f"Baseline (predict mean {train_mean:.2f}) MAE: {baseline_mae:.3f}")

    # Training loop
    best_val_mae = float("inf")
    best_model_state = None

    for epoch in range(epochs):
        # Train
        model.train()
        train_loss = 0.0
        for X_batch, y_batch in train_loader:
            X_batch = X_batch.to(device)
            y_batch = y_batch.to(device)

            optimizer.zero_grad()
            y_pred = model(X_batch).squeeze()
            loss = criterion(y_pred, y_batch)
            loss.backward()
            optimizer.step()

            train_loss += loss.item() * X_batch.shape[0]

        train_loss /= len(train_dataset)

        # Validate
        if val_loader:
            model.eval()
            with torch.no_grad():
                val_pred = []
                val_true = []
                for X_batch, y_batch in val_loader:
                    X_batch = X_batch.to(device)
                    y_batch = y_batch.to(device)
                    y_pred = model(X_batch).squeeze()
                    val_pred.append(y_pred.cpu())
                    val_true.append(y_batch.cpu())

                val_pred = torch.cat(val_pred)
                val_true = torch.cat(val_true)
                val_mae = torch.abs(val_pred - val_true).mean().item()

                if val_mae < best_val_mae:
                    best_val_mae = val_mae
                    # deepcopy: state_dict() alone returns tensor views, so
                    # "best" would silently alias whatever epoch runs last.
                    best_model_state = copy.deepcopy(model.state_dict())

                print(f"Epoch {epoch + 1}/{epochs} | Train loss: {train_loss:.4f} | Val MAE: {val_mae:.3f}")
        else:
            print(f"Epoch {epoch + 1}/{epochs} | Train loss: {train_loss:.4f}")

    # Restore best model
    if best_model_state:
        model.load_state_dict(best_model_state)

    # Final evaluation
    if val_loader:
        model.eval()
        with torch.no_grad():
            val_pred = []
            val_true = []
            for X_batch, y_batch in val_loader:
                X_batch = X_batch.to(device)
                y_batch = y_batch.to(device)
                y_pred = model(X_batch).squeeze()
                val_pred.append(y_pred.cpu())
                val_true.append(y_batch.cpu())

            val_pred = torch.cat(val_pred)
            val_true = torch.cat(val_true)
            val_mae = torch.abs(val_pred - val_true).mean().item()
            print(f"\nFinal Val MAE: {val_mae:.3f}")
            print(f"Baseline MAE: {baseline_mae:.3f}")

            # Bucket accuracy + per-bucket recall. bucket_of returns strings, so
            # this compares them directly instead of routing through
            # torch.tensor(...) (which cannot hold a list of str).
            preds_b = [bucket_of(p.item()) for p in val_pred]
            trues_b = [bucket_of(t.item()) for t in val_true]
            bucket_acc = sum(p == t for p, t in zip(preds_b, trues_b)) / max(len(preds_b), 1)
            print(f"Bucket accuracy: {bucket_acc:.1%} (vs. 33% random)")
            for b in BUCKETS:
                true_in_bucket = sum(t == b for t in trues_b)
                correct_in_bucket = sum(p == t == b for p, t in zip(preds_b, trues_b))
                recall = correct_in_bucket / max(true_in_bucket, 1)
                print(f"  {b} recall: {recall:.1%} ({correct_in_bucket}/{true_in_bucket})")

    # Export to TorchScript
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    # Trace on an example (1, 64, 34)
    example = torch.randn(1, 64, 34).to(device)
    traced = torch.jit.trace(model, example)
    traced.save(str(out_path))
    print(f"Exported TorchScript to {out_path}")


if __name__ == "__main__":
    main()
