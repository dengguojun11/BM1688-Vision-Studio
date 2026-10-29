from __future__ import annotations

from pathlib import Path

import numpy as np
from PIL import Image


def extract_features(image_path: Path) -> np.ndarray | None:
    import cv2

    img = cv2.imread(str(image_path))
    if img is None:
        return None
    img = cv2.resize(img, (256, 256))
    hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
    hist_h = cv2.calcHist([hsv], [0], None, [8], [0, 180])
    hist_s = cv2.calcHist([hsv], [1], None, [8], [0, 256])
    hist_v = cv2.calcHist([hsv], [2], None, [8], [0, 256])
    hist_color = np.concatenate([hist_h.flatten(), hist_s.flatten(), hist_v.flatten()])
    hist_color = hist_color / (np.sum(hist_color) + 1e-8)
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    sobelx = cv2.Sobel(gray, cv2.CV_64F, 1, 0, ksize=3)
    sobely = cv2.Sobel(gray, cv2.CV_64F, 0, 1, ksize=3)
    angle = np.arctan2(sobely, sobelx) * (180 / np.pi) % 180
    hist_edge = cv2.calcHist([angle.astype(np.float32)], [0], None, [8], [0, 180])
    hist_edge = hist_edge.flatten()
    hist_edge = hist_edge / (np.sum(hist_edge) + 1e-8)
    return np.concatenate([hist_color, hist_edge]).astype(np.float32)


def validate_image_readable(image_path: Path) -> bool:
    """空文件或损坏图片返回 False；避免对空缓冲调用 imdecode 抛错。"""
    try:
        if not image_path.is_file():
            return False
        raw = image_path.read_bytes()
        if len(raw) < 12:
            return False

        with Image.open(image_path) as image:
            image.verify()

        try:
            import cv2
        except ModuleNotFoundError:
            return True

        arr = np.frombuffer(raw, dtype=np.uint8)
        img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
        return img is not None and img.size > 0
    except Exception:
        return False


def image_similarity(img_path1: Path, img_path2: Path, method: str = "hist") -> float:
    import cv2

    img1 = cv2.imread(str(img_path1))
    img2 = cv2.imread(str(img_path2))
    if img1 is None or img2 is None:
        return 0.0
    if method == "ssim":
        try:
            from skimage.metrics import structural_similarity as ssim
        except ImportError:
            method = "hist"
        else:
            gray1 = cv2.cvtColor(img1, cv2.COLOR_BGR2GRAY)
            gray2 = cv2.cvtColor(img2, cv2.COLOR_BGR2GRAY)
            h = min(gray1.shape[0], gray2.shape[0])
            w = min(gray1.shape[1], gray2.shape[1])
            gray1 = cv2.resize(gray1, (w, h))
            gray2 = cv2.resize(gray2, (w, h))
            score, _ = ssim(gray1, gray2, full=True)
            return float(score)
    hsv1 = cv2.cvtColor(img1, cv2.COLOR_BGR2HSV)
    hsv2 = cv2.cvtColor(img2, cv2.COLOR_BGR2HSV)
    hist1 = cv2.calcHist([hsv1], [0, 1, 2], None, [50, 60, 60], [0, 180, 0, 256, 0, 256])
    hist2 = cv2.calcHist([hsv2], [0, 1, 2], None, [50, 60, 60], [0, 180, 0, 256, 0, 256])
    cv2.normalize(hist1, hist1, 0, 1, cv2.NORM_MINMAX)
    cv2.normalize(hist2, hist2, 0, 1, cv2.NORM_MINMAX)
    return max(0.0, float(cv2.compareHist(hist1, hist2, cv2.HISTCMP_CORREL)))
