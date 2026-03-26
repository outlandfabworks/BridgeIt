"""
remove_bg.py — Background removal stage.

Automatically detects whether the image is a flat graphic/logo (uniform
background colour) or a photo, and uses the appropriate method:

  • Flat graphic / logo  →  fast luminance-threshold approach (clean, sharp)
  • Photo                →  rembg U2Net AI model

This gives far better results for logos like the Outland Fabworks mark
where the background is a near-uniform dark colour.
"""

from __future__ import annotations

import io
from pathlib import Path
from typing import Union

import numpy as np
from PIL import Image


def remove_background(source: Union[str, Path, Image.Image]) -> Image.Image:
    """Remove the background from an image.

    Args:
        source: File path (str/Path) or an already-loaded PIL Image.

    Returns:
        PIL Image in RGBA mode with background made transparent.

    Raises:
        FileNotFoundError: If a path is given and the file does not exist.
        ValueError: If the image cannot be processed.
        RuntimeError: If rembg fails and no fallback can be applied.
    """
    img = _load_image(source)

    if _is_flat_graphic(img):
        return _threshold_removal(img)
    else:
        return _rembg_removal(img)


# ---------------------------------------------------------------------------
# Detection
# ---------------------------------------------------------------------------

def _is_flat_graphic(img: Image.Image) -> bool:
    """Return True if the image looks like a logo / flat graphic.

    Heuristic: sample the four corners.  If they are all very similar in
    colour, the image almost certainly has a uniform background.
    """
    arr = np.array(img.convert("RGB"), dtype=np.float32)
    h, w = arr.shape[:2]
    r = max(1, min(h, w) // 20)   # sample region size

    corners = [
        arr[:r,  :r ].mean(axis=(0, 1)),
        arr[:r,  -r:].mean(axis=(0, 1)),
        arr[-r:, :r ].mean(axis=(0, 1)),
        arr[-r:, -r:].mean(axis=(0, 1)),
    ]
    corners_arr = np.array(corners)   # shape (4, 3)
    max_spread = float(np.max(np.std(corners_arr, axis=0)))
    return max_spread < 25.0          # low variance → uniform background


# ---------------------------------------------------------------------------
# Method 1: threshold removal (logos / flat graphics)
# ---------------------------------------------------------------------------

def _threshold_removal(img: Image.Image) -> Image.Image:
    """Strip a near-uniform background by colour distance thresholding.

    Works by sampling the background colour from the image corners, then
    marking every pixel whose colour is *close* to that background colour
    as transparent.
    """
    rgb = img.convert("RGB")
    arr = np.array(rgb, dtype=np.float32)
    h, w = arr.shape[:2]
    r = max(1, min(h, w) // 20)

    # Estimate background colour as the average of the four corner regions
    corners = [
        arr[:r,  :r ],
        arr[:r,  -r:],
        arr[-r:, :r ],
        arr[-r:, -r:],
    ]
    bg_color = np.mean(np.concatenate([c.reshape(-1, 3) for c in corners], axis=0), axis=0)

    # Per-pixel Euclidean distance from background colour
    diff = arr - bg_color                             # (H, W, 3)
    dist = np.sqrt(np.sum(diff ** 2, axis=2))         # (H, W)

    # Threshold: pixels close to bg → transparent
    threshold = 40.0
    alpha = np.clip((dist - threshold) * 6, 0, 255).astype(np.uint8)

    # Morphological cleanup to smooth jagged alpha edges
    import cv2
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3))
    alpha = cv2.morphologyEx(alpha, cv2.MORPH_CLOSE, kernel, iterations=2)

    rgba = np.dstack([arr.astype(np.uint8), alpha])
    return Image.fromarray(rgba, "RGBA")


# ---------------------------------------------------------------------------
# Method 2: rembg AI removal (photos)
# ---------------------------------------------------------------------------

def _rembg_removal(img: Image.Image) -> Image.Image:
    try:
        from rembg import remove as rembg_remove
    except ImportError as exc:
        raise RuntimeError("rembg is not installed. Run: pip install rembg") from exc

    img_bytes = _image_to_bytes(img)
    try:
        result_bytes = rembg_remove(img_bytes)
    except Exception as exc:
        raise RuntimeError(
            f"Background removal failed: {exc}\n"
            "Check your internet connection — rembg needs to download the AI model on first use."
        ) from exc

    return Image.open(io.BytesIO(result_bytes)).convert("RGBA")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _load_image(source: Union[str, Path, Image.Image]) -> Image.Image:
    if isinstance(source, Image.Image):
        return source.copy()

    path = Path(source)
    if not path.exists():
        raise FileNotFoundError(f"Image file not found: {path}")
    if path.suffix.lower() not in {".png", ".jpg", ".jpeg", ".webp", ".bmp"}:
        raise ValueError(f"Unsupported image format: {path.suffix}")

    return Image.open(path)


def _image_to_bytes(img: Image.Image) -> bytes:
    buf = io.BytesIO()
    fmt = "PNG" if img.mode in ("RGBA", "P") else "JPEG"
    img.save(buf, format=fmt)
    return buf.getvalue()


# ---------------------------------------------------------------------------
# Standalone validation
# ---------------------------------------------------------------------------

def _validate(image_path: str) -> None:
    print(f"[remove_bg] Processing: {image_path}")
    img = _load_image(image_path)
    flat = _is_flat_graphic(img)
    print(f"[remove_bg] Detected as: {'flat graphic' if flat else 'photo'}")

    result = remove_background(image_path)
    out_path = Path(image_path).with_stem(Path(image_path).stem + "_nobg").with_suffix(".png")
    result.save(out_path)
    print(f"[remove_bg] Saved: {out_path}  size={result.size}  mode={result.mode}")

    alpha = result.split()[3]
    pixels = alpha.tobytes()
    transparent = pixels.count(b"\x00")
    total = len(pixels)
    print(f"[remove_bg] Transparent: {transparent}/{total} ({100*transparent/total:.1f}%)")
    print("[remove_bg] PASS" if transparent > 0 else "[remove_bg] FAIL — no transparent pixels")


if __name__ == "__main__":
    import sys
    if len(sys.argv) < 2:
        print("Usage: python remove_bg.py <image_path>")
        sys.exit(1)
    _validate(sys.argv[1])
