"""
remove_bg.py — AI background removal stage.

Uses rembg (U2Net) to strip backgrounds from PNG/JPG images and
return a PIL Image with an alpha channel. Falls back gracefully if
rembg model download fails.
"""

from __future__ import annotations

import io
from pathlib import Path
from typing import Union

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
    try:
        from rembg import remove as rembg_remove
    except ImportError as exc:
        raise RuntimeError(
            "rembg is not installed. Run: pip install rembg"
        ) from exc

    img = _load_image(source)

    # rembg works on raw bytes
    img_bytes = _image_to_bytes(img)
    try:
        result_bytes = rembg_remove(img_bytes)
    except Exception as exc:
        raise RuntimeError(
            f"Background removal failed: {exc}\n"
            "Check your internet connection — rembg needs to download the AI model on first use."
        ) from exc

    result = Image.open(io.BytesIO(result_bytes)).convert("RGBA")
    return result


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
    # Preserve format; rembg handles PNG transparency correctly
    fmt = "PNG" if img.mode in ("RGBA", "P") else "JPEG"
    img.save(buf, format=fmt)
    return buf.getvalue()


# ---------------------------------------------------------------------------
# Standalone validation helper
# ---------------------------------------------------------------------------

def _validate(image_path: str) -> None:
    """Quick CLI validation — run as: python -m bridgeit.pipeline.remove_bg <image>"""
    import sys
    from pathlib import Path

    print(f"[remove_bg] Processing: {image_path}")
    result = remove_background(image_path)

    out_path = Path(image_path).with_stem(Path(image_path).stem + "_nobg").with_suffix(".png")
    result.save(out_path)
    print(f"[remove_bg] Saved: {out_path}")
    print(f"[remove_bg] Size: {result.size}, mode: {result.mode}")

    # Verify transparency exists
    if result.mode != "RGBA":
        print("[remove_bg] WARNING: result is not RGBA — no transparency channel")
    else:
        alpha = result.split()[3]
        pixels = alpha.tobytes()
        transparent = pixels.count(b"\x00")
        total = len(pixels)
        print(f"[remove_bg] Transparency: {transparent}/{total} pixels ({100*transparent/total:.1f}%) are fully transparent")
        print("[remove_bg] PASS" if transparent > 0 else "[remove_bg] FAIL — no transparent pixels found")


if __name__ == "__main__":
    import sys
    if len(sys.argv) < 2:
        print("Usage: python remove_bg.py <image_path>")
        sys.exit(1)
    _validate(sys.argv[1])
