"""
captcha_solver.py — Solve COA CAPTCHA images via local ddddocr engine.

Fast, local, 100% free with zero API keys or rate limits.
Uses PIL + numpy + ddddocr + onnxruntime.
"""

import sys
import types
import io
import logging
import re
import numpy as np
from PIL import Image
from typing import Optional

# ─────────────────────────────────────────────────────────────────────────────
# CV2 Stub (PIL-based) to satisfy ddddocr imports without OpenCV C++ binaries
# ─────────────────────────────────────────────────────────────────────────────
if "cv2" not in sys.modules:
    try:
        import cv2
    except ImportError:
        fake_cv2 = types.ModuleType("cv2")

        def _imdecode(buf, flags):
            img = Image.open(io.BytesIO(buf))
            return np.array(img)

        def _cvtColor(src, code):
            if len(src.shape) == 3 and src.shape[2] == 3:
                return np.dot(src[..., :3], [0.2989, 0.5870, 0.1140]).astype(np.uint8)
            return src

        def _resize(src, dsize, fx=0, fy=0, interpolation=None):
            img = Image.fromarray(src)
            w, h = dsize
            if w == 0 or h == 0:
                w = int(src.shape[1] * fx)
                h = int(src.shape[0] * fy)
            res = img.resize((w, h), Image.Resampling.BILINEAR)
            return np.array(res)

        def _threshold(src, thresh, maxval, type):
            res = (src > thresh).astype(np.uint8) * maxval
            return thresh, res

        fake_cv2.imdecode = _imdecode
        fake_cv2.cvtColor = _cvtColor
        fake_cv2.resize = _resize
        fake_cv2.threshold = _threshold
        fake_cv2.COLOR_BGR2GRAY = 6
        fake_cv2.COLOR_RGB2GRAY = 7
        fake_cv2.INTER_AREA = 3
        fake_cv2.INTER_LINEAR = 1
        fake_cv2.THRESH_BINARY = 0

        sys.modules["cv2"] = fake_cv2

import ddddocr
import config
import threading

logger = logging.getLogger(__name__)

# Lazy-init ddddocr instance & thread lock
_ocr = None
_ocr_lock = threading.Lock()


def _get_ocr():
    """Lazy-initialize ddddocr engine in a thread-safe manner."""
    global _ocr
    if _ocr is None:
        with _ocr_lock:
            if _ocr is None:
                _ocr = ddddocr.DdddOcr(show_ad=False)
    return _ocr


def is_valid_captcha(text: str) -> bool:
    """Validate that the returned text is 5 or 6 alphanumeric characters."""
    return bool(re.fullmatch(r"[A-Za-z0-9]{5,6}", text.strip()))


def solve_captcha(image_bytes: bytes) -> Optional[str]:
    """
    Solve CAPTCHA using local ddddocr engine (thread-safe).

    Returns:
        String CAPTCHA text on success, or None on failure.
    """
    if not image_bytes:
        logger.error("solve_captcha received empty image bytes")
        return None

    try:
        ocr = _get_ocr()
        with _ocr_lock:
            res = ocr.classification(image_bytes)
        if not res:
            logger.warning("ddddocr returned empty result")
            return None

        answer = res.strip()
        if is_valid_captcha(answer):
            logger.info("ddddocr solved CAPTCHA: %s", answer)
            return answer

        matches = re.findall(r"[A-Za-z0-9]{5,6}", answer)
        if matches:
            candidate = matches[0]
            logger.info("ddddocr candidate extracted: %s", candidate)
            return candidate

        return answer

    except Exception as e:
        logger.error("ddddocr error: %s", e)
        return None
