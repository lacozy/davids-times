from __future__ import annotations

import base64
import io
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Optional
from urllib.parse import urlparse

import requests
from PIL import Image, ImageEnhance, ImageOps

from .models import Article

logger = logging.getLogger(__name__)

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/131.0.0.0 Safari/537.36"
    ),
    "Accept": "image/avif,image/webp,image/apng,image/*,*/*;q=0.8",
}

SKIP_URL_SUBSTRINGS = (
    "logo",
    "favicon",
    "avatar",
    "sprite",
    "pixel",
    "1x1",
    "tracker",
    "badge",
    "icon",
    "placeholder",
    ".svg",
    "doubleclick",
    "gravatar",
)


def is_usable_image_url(url: Optional[str]) -> bool:
    if not url or not url.startswith(("http://", "https://")):
        return False
    lower = url.lower()
    if any(token in lower for token in SKIP_URL_SUBSTRINGS):
        return False
    return True


def process_image_for_eink(
    image_bytes: bytes,
    *,
    max_width: int = 920,
    target_aspect: Optional[float] = None,
    grayscale: bool = True,
    contrast: float = 1.22,
    sharpness: float = 1.35,
    jpeg_quality: int = 80,
) -> Optional[tuple[str, bool]]:
    """Convert a downloaded photo into a crisp e-ink optimized base64 JPEG data URI."""
    try:
        with Image.open(io.BytesIO(image_bytes)) as img:
            img = ImageOps.exif_transpose(img)
            w, h = img.size
            if w < 260 or h < 140:
                return None

            ratio = w / float(h)
            if ratio > 3.4 or ratio < 0.48:
                return None

            # Flatten transparency onto crisp paper white
            if img.mode in ("RGBA", "LA") or (img.mode == "P" and "transparency" in img.info):
                bg = Image.new("RGB", img.size, (255, 255, 255))
                rgba = img.convert("RGBA")
                bg.paste(rgba, mask=rgba.split()[-1])
                img = bg
            elif img.mode != "RGB":
                img = img.convert("RGB")

            # Crop overly tall images so they never swallow an entire Kindle page
            if target_aspect is not None:
                img = ImageOps.fit(img, (max_width, int(max_width / target_aspect)), method=Image.Resampling.LANCZOS)
            else:
                if ratio < 1.15:
                    # Center-crop tall portrait photos to a balanced 4:3 print frame
                    crop_h = int(w / 1.33)
                    top = max(0, (h - crop_h) // 3)  # bias slightly upward to preserve faces
                    img = img.crop((0, top, w, min(h, top + crop_h)))
                if img.width > max_width:
                    new_h = max(1, int(img.height * (max_width / img.width)))
                    img = img.resize((max_width, new_h), Image.Resampling.LANCZOS)

            if grayscale:
                img = ImageOps.grayscale(img)
                img = ImageOps.autocontrast(img, cutoff=1)
                img = ImageEnhance.Contrast(img).enhance(contrast)
                img = ImageEnhance.Sharpness(img).enhance(sharpness)

            buf = io.BytesIO()
            img.save(buf, format="JPEG", quality=jpeg_quality, optimize=True)
            encoded = base64.b64encode(buf.getvalue()).decode("ascii")
            is_wide = (img.width / float(img.height)) >= 1.2
            return f"data:image/jpeg;base64,{encoded}", is_wide
    except Exception as exc:
        logger.debug("Image processing failed: %s", exc)
        return None


def _fetch_single_article_image(
    article: Article,
    device_cfg: dict[str, Any],
    timeout: int = 10,
) -> None:
    if not is_usable_image_url(article.image_url):
        return
    assert article.image_url is not None

    parsed = urlparse(article.url)
    headers = dict(HEADERS)
    if parsed.scheme and parsed.netloc:
        headers["Referer"] = f"{parsed.scheme}://{parsed.netloc}/"

    try:
        resp = requests.get(article.image_url, headers=headers, timeout=timeout, stream=True)
        resp.raise_for_status()
        content_type = resp.headers.get("Content-Type", "").lower()
        if "image" not in content_type and "octet-stream" not in content_type:
            return
        # Cap download at 8 MB to stay fast and safe
        chunks = []
        total = 0
        for chunk in resp.iter_content(chunk_size=32768):
            total += len(chunk)
            if total > 8 * 1024 * 1024:
                return
            chunks.append(chunk)
        raw = b"".join(chunks)

        max_w = 960 if article.is_front_page else 760
        target_aspect = 1.68 if article.is_front_page else None
        result = process_image_for_eink(
            raw,
            max_width=max_w,
            target_aspect=target_aspect,
            grayscale=bool(device_cfg.get("grayscale_images", True)),
            contrast=float(device_cfg.get("eink_contrast", 1.22)),
            sharpness=float(device_cfg.get("eink_sharpness", 1.35)),
        )
        if result:
            article.image_data_uri, article.image_is_wide = result
    except Exception as exc:
        logger.debug("Could not download image %s: %s", article.image_url, exc)


def hydrate_article_images(
    articles: list[Article],
    device_cfg: dict[str, Any],
    max_workers: int = 8,
) -> None:
    """Download and e-ink optimize images for selected articles concurrently."""
    targets = [a for a in articles if a.image_url]
    if not targets:
        return
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = [pool.submit(_fetch_single_article_image, art, device_cfg) for art in targets]
        for fut in as_completed(futures):
            try:
                fut.result()
            except Exception:
                pass
