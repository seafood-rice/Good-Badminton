"""Thin inference wrapper around the vendored BST_CG_AP stroke-type model.

Model: ``BST_CG_AP`` from ``third_party/bst/model/bst.py``, pretrained on
ShuttleSet with the 25-class merged label set. See
``third_party/bst/CONTRACT.md`` for the exact input/output tensor shapes and
the ordered ``CLASS_NAMES`` this module assumes -- that file is the
authoritative source; the constants below just mirror it for convenience.

Torch is a heavy, optional-at-import-time dependency, so it is imported
lazily inside the functions below rather than at module scope.
"""

from pathlib import Path

import numpy as np

_THIRD_PARTY_BST_DIR = Path(__file__).resolve().parents[2] / "third_party" / "bst"

# Mirrors third_party/bst/CONTRACT.md -- keep these two files in sync.
SEQ_LEN = 30
N_CLASSES = 25
N_PEOPLE = 2
POSE_IN_DIM = 72  # (17 joints + 19 bones) * 2 (x, y), pose_style="JnB_bone"


def _add_vendored_bst_to_path():
    import sys

    path_str = str(_THIRD_PARTY_BST_DIR)
    if path_str not in sys.path:
        sys.path.insert(0, path_str)


def load_bst(weights_path):
    """Build the vendored BST_CG_AP model and load pretrained weights for CPU inference.

    Parameters
    ----------
    weights_path : str or pathlib.Path
        Path to the ShuttleSet-25-merged checkpoint (a plain ``state_dict``
        ``.pt`` file, e.g. ``weights/bst-shuttleset.pt``).

    Returns
    -------
    torch.nn.Module
        The model in ``eval()`` mode on CPU.
    """
    import torch

    _add_vendored_bst_to_path()
    from model.bst import BST_CG_AP

    model = BST_CG_AP(
        in_dim=POSE_IN_DIM,
        n_class=N_CLASSES,
        seq_len=SEQ_LEN,
        depth_tem=2,
        depth_inter=1,
    )
    state_dict = torch.load(str(weights_path), map_location="cpu", weights_only=True)
    model.load_state_dict(state_dict)
    model.eval()
    return model


def predict(model, pose, shuttle, positions):
    """Run one forward pass and return the raw (N_CLASSES,) class logits.

    Parameters
    ----------
    model : torch.nn.Module
        A model returned by `load_bst`.
    pose : array-like, shape (SEQ_LEN, N_PEOPLE, POSE_IN_DIM)
        Per-frame joint+bone features for both players, already normalized
        and padded/truncated to SEQ_LEN frames (see CONTRACT.md).
    shuttle : array-like, shape (SEQ_LEN, 2)
        Per-frame shuttlecock (x, y) position, normalized to [0, 1].
    positions : array-like, shape (SEQ_LEN, N_PEOPLE, 2)
        Per-frame player court position (x, y), normalized to [0, 1].

    Returns
    -------
    np.ndarray, shape (N_CLASSES,)
        Raw (pre-softmax) class logits, float32.

    Notes
    -----
    The whole window is treated as valid (``video_len = SEQ_LEN``); callers
    with a shorter true window should zero-pad up to SEQ_LEN as documented in
    CONTRACT.md rather than passing a shorter array.
    """
    import torch

    pose_t = torch.as_tensor(np.asarray(pose), dtype=torch.float32).unsqueeze(0)
    shuttle_t = torch.as_tensor(np.asarray(shuttle), dtype=torch.float32).unsqueeze(0)
    positions_t = torch.as_tensor(np.asarray(positions), dtype=torch.float32).unsqueeze(0)
    video_len = torch.tensor([pose_t.shape[1]], dtype=torch.long)

    with torch.no_grad():
        logits = model(pose_t, shuttle_t, positions_t, video_len)

    return logits.squeeze(0).cpu().numpy()
