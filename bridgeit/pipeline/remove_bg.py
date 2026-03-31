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
from typing import List, Tuple, Union

import numpy as np
from PIL import Image

# Maximum pixel dimension for AI processing.  Large images are downscaled
# before rembg to prevent the contour-tracing stage from hanging on the
# massive noisy alpha masks that the AI produces at full resolution.
_MAX_PROCESS_DIM = 1500


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
    # Load whatever form of input was given into a PIL Image object
    img = _load_image(source)

    # Downscale very large images before processing — rembg and the
    # subsequent contour trace both suffer badly on huge images.
    img = _cap_size(img)

    # Choose the removal strategy based on whether this looks like a logo
    if _is_flat_graphic(img):
        # Logos/flat graphics: use the fast colour-threshold approach
        return _threshold_removal(img)
    else:
        # Real photos: use the AI model for accurate subject detection
        return _rembg_removal(img)


def color_erase_removal(
    img: Image.Image,
    colors: List[Tuple[int, int, int]],
    tolerance: float = 50.0,
) -> Image.Image:
    """Remove background by making pixels near any of the given colours transparent.

    Useful for images where the auto-detection fails (complex/gradient backgrounds).
    The user picks representative background colours; this function removes all
    pixels within `tolerance` Euclidean distance in RGB space.

    A soft transition is applied around the tolerance edge to avoid harsh cutouts.
    Morphological cleanup removes isolated specks left by the colour mask.

    Args:
        img:       Source image (any mode — converted to RGBA internally).
        colors:    List of (R, G, B) tuples sampled from the background.
        tolerance: Euclidean RGB distance within which pixels are erased.

    Returns:
        RGBA image with matching background pixels made transparent.
    """
    rgb = np.array(img.convert("RGB"), dtype=np.float32)
    h, w = rgb.shape[:2]

    # Start with full opacity
    alpha = np.ones((h, w), dtype=np.float32) * 255.0

    half_tol = tolerance * 0.5
    for r, g, b in colors:
        diff = rgb - np.array([r, g, b], dtype=np.float32)
        dist = np.sqrt(np.sum(diff ** 2, axis=2))
        # Pixels inside half-tolerance → fully transparent
        # Pixels between half and full tolerance → fade out linearly
        fade = np.clip((dist - half_tol) / half_tol, 0.0, 1.0)
        alpha = np.minimum(alpha, fade * 255.0)

    alpha_u8 = alpha.astype(np.uint8)

    # Clean up speckle with a pure-PIL blur+threshold.
    # The soft fade creates noisy gradients at colour boundaries that generate
    # thousands of tiny contour fragments, hanging the trace stage.
    # Blurring merges nearby fragments; hard-thresholding gives clean edges.
    from PIL import ImageFilter as _IF
    alpha_pil = Image.fromarray(alpha_u8, "L")
    alpha_pil = alpha_pil.filter(_IF.GaussianBlur(radius=2))
    alpha_u8 = np.where(np.array(alpha_pil) > 127, 255, 0).astype(np.uint8)

    rgba = np.dstack([rgb.astype(np.uint8), alpha_u8])
    return Image.fromarray(rgba, "RGBA")


# ---------------------------------------------------------------------------
# Size cap
# ---------------------------------------------------------------------------

def _cap_size(img: Image.Image) -> Image.Image:
    """Downscale if the image's largest dimension exceeds _MAX_PROCESS_DIM.

    Prevents rembg from spending forever on huge images and stops the
    subsequent contour-tracing stage from generating millions of tiny paths.
    """
    w, h = img.size
    if max(w, h) <= _MAX_PROCESS_DIM:
        return img
    scale = _MAX_PROCESS_DIM / max(w, h)
    new_size = (int(w * scale), int(h * scale))
    return img.resize(new_size, Image.LANCZOS)


# ---------------------------------------------------------------------------
# Detection
# ---------------------------------------------------------------------------

def _is_flat_graphic(img: Image.Image) -> bool:
    """Return True if the image looks like a logo / flat graphic.

    Heuristic: sample the four corners.  If they are all very similar in
    colour, the image almost certainly has a uniform background.
    """
    # Convert to RGB (3 channels) so we can measure colour variation
    arr = np.array(img.convert("RGB"), dtype=np.float32)
    h, w = arr.shape[:2]

    # r is the size of the corner sample region (a small fraction of the image)
    r = max(1, min(h, w) // 20)   # sample region size

    # Sample the average RGB colour from each corner of the image
    corners = [
        arr[:r,  :r ].mean(axis=(0, 1)),   # top-left corner
        arr[:r,  -r:].mean(axis=(0, 1)),   # top-right corner
        arr[-r:, :r ].mean(axis=(0, 1)),   # bottom-left corner
        arr[-r:, -r:].mean(axis=(0, 1)),   # bottom-right corner
    ]

    # Stack the four (R,G,B) averages into a 4×3 array
    corners_arr = np.array(corners)   # shape (4, 3)

    # max_spread is the largest colour-channel standard deviation across corners.
    # A low value means all corners look similar → uniform background.
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
    # Work in RGB for colour distance calculations
    rgb = img.convert("RGB")
    arr = np.array(rgb, dtype=np.float32)
    h, w = arr.shape[:2]

    # The sample region size — a small patch from each corner
    r = max(1, min(h, w) // 20)

    # Collect the corner pixel patches for averaging
    corners = [
        arr[:r,  :r ],    # top-left patch
        arr[:r,  -r:],    # top-right patch
        arr[-r:, :r ],    # bottom-left patch
        arr[-r:, -r:],    # bottom-right patch
    ]

    # Estimate background colour as the average of the four corner regions.
    # np.concatenate stacks all patches into one long list of (R,G,B) rows.
    bg_color = np.mean(np.concatenate([c.reshape(-1, 3) for c in corners], axis=0), axis=0)

    # For each pixel, compute how far its colour is from the background colour.
    # diff is the per-channel difference; dist collapses to a single distance.
    diff = arr - bg_color                             # (H, W, 3)
    dist = np.sqrt(np.sum(diff ** 2, axis=2))         # (H, W)

    # Convert distance to alpha (opacity):
    # pixels very close to bg_color get alpha=0 (transparent);
    # pixels far away get alpha=255 (fully opaque).
    # The * 6 factor sharpens the transition edge.
    threshold = 40.0
    alpha = np.clip((dist - threshold) * 6, 0, 255).astype(np.uint8)

    # Morphological cleanup: MORPH_CLOSE fills small holes in the alpha mask,
    # smoothing out jagged edges left by the per-pixel threshold.
    import cv2
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3))
    alpha = cv2.morphologyEx(alpha, cv2.MORPH_CLOSE, kernel, iterations=2)

    # Combine the original RGB data with our computed alpha channel into RGBA
    rgba = np.dstack([arr.astype(np.uint8), alpha])
    return Image.fromarray(rgba, "RGBA")


# ---------------------------------------------------------------------------
# Method 2: rembg AI removal (photos)
# ---------------------------------------------------------------------------

def _rembg_removal(img: Image.Image) -> Image.Image:
    # rembg is an optional dependency that wraps the U2Net AI segmentation model.
    # It's kept as a lazy import so the app starts even if rembg isn't installed.
    try:
        from rembg import remove as rembg_remove
    except ImportError as exc:
        raise RuntimeError("rembg is not installed. Run: pip install rembg") from exc

    # rembg operates on raw image bytes (PNG/JPEG), not PIL objects
    img_bytes = _image_to_bytes(img)
    try:
        # rembg returns RGBA bytes with the background erased by the AI model
        result_bytes = rembg_remove(img_bytes)
    except Exception as exc:
        raise RuntimeError(
            f"Background removal failed: {exc}\n"
            "Check your internet connection — rembg needs to download the AI model on first use."
        ) from exc

    # Wrap the result bytes back into a PIL Image in RGBA mode
    return Image.open(io.BytesIO(result_bytes)).convert("RGBA")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _load_image(source: Union[str, Path, Image.Image]) -> Image.Image:
    # If the caller already has a PIL Image, make a copy so we don't mutate
    # the original (PIL images are mutable).
    if isinstance(source, Image.Image):
        return source.copy()

    path = Path(source)

    # Raise a clear error if the file doesn't exist, rather than a cryptic
    # PIL error later in the chain.
    if not path.exists():
        raise FileNotFoundError(f"Image file not found: {path}")

    # Reject unsupported formats early so the error message is informative
    if path.suffix.lower() not in {".png", ".jpg", ".jpeg", ".webp", ".bmp"}:
        raise ValueError(f"Unsupported image format: {path.suffix}")

    return Image.open(path)


def _image_to_bytes(img: Image.Image) -> bytes:
    # Write the PIL image into an in-memory byte buffer.
    # rembg and other tools accept bytes, not PIL objects.
    buf = io.BytesIO()

    # Use PNG for images that have transparency (RGBA or palette),
    # JPEG for regular RGB photos (smaller file, faster to process).
    fmt = "PNG" if img.mode in ("RGBA", "P") else "JPEG"
    img.save(buf, format=fmt)
    return buf.getvalue()


# ---------------------------------------------------------------------------
# Standalone validation
# ---------------------------------------------------------------------------

def _validate(image_path: str) -> None:
    # This function is only called when running this file directly (see __main__).
    # It exercises the full background removal flow and prints diagnostic info.
    print(f"[remove_bg] Processing: {image_path}")
    img = _load_image(image_path)
    flat = _is_flat_graphic(img)
    print(f"[remove_bg] Detected as: {'flat graphic' if flat else 'photo'}")

    result = remove_background(image_path)

    # Save the result next to the input file with a _nobg suffix
    out_path = Path(image_path).with_stem(Path(image_path).stem + "_nobg").with_suffix(".png")
    result.save(out_path)
    print(f"[remove_bg] Saved: {out_path}  size={result.size}  mode={result.mode}")

    # Count transparent pixels (alpha == 0) to verify background was removed
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
