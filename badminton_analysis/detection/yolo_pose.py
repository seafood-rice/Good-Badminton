class YOLOPoseProcessor:
    """Ultralytics YOLO pose processor with COCO 17 keypoint output."""

    def __init__(self, model_path="yolo11n-pose.pt", device="auto", conf=0.25):
        from ultralytics import YOLO

        self.model_path = model_path
        self.conf = conf
        self.inference_name = "YOLO-Pose"
        if device in (None, "auto"):
            selected = "cpu"
            try:
                import torch
                if torch.cuda.is_available():
                    selected = 0
            except Exception:
                selected = "cpu"
            self.device = selected
        else:
            self.device = device

        print(f"Initializing YOLO pose model (model: {self.model_path}, device: {self.device})")
        self.model = YOLO(self.model_path)

    def process_frame(self, frame, imgsz=None):
        """Detect people in ``frame``.

        ``imgsz`` is the inference size Ultralytics letterboxes to. Leaving it
        None keeps Ultralytics' 640 default, which is what shipped -- and which
        shrinks a 3840-wide frame by 6x, putting a distant player below the
        detector's reach. Callers cropping to a small region pass a size
        matched to that crop instead.
        """
        kwargs = {"conf": self.conf, "device": self.device, "verbose": False}
        if imgsz:
            kwargs["imgsz"] = int(imgsz)
        result = self.model(frame, **kwargs)[0]
        if result.keypoints is None or result.keypoints.xy is None:
            return None, None

        keypoints = result.keypoints.xy
        scores = result.keypoints.conf
        if keypoints.shape[0] == 0:
            return None, None

        keypoints = keypoints.detach().cpu().numpy()
        scores = scores.detach().cpu().numpy() if scores is not None else None
        return keypoints, scores
