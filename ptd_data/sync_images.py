#!/usr/bin/env python3
"""
Unified athlete image pipeline: download → crop → resize → WebP → R2 upload.

Usage:
    python ptd_data/sync_images.py            # incremental: skip already-processed/uploaded files
    python ptd_data/sync_images.py --clear    # wipe R2 prefix + local WebPs, full reprocess from raw
"""
from __future__ import annotations

import argparse
import shutil

import boto3
import cv2
import duckdb
import numpy as np
import requests
from botocore.config import Config
from PIL import Image, UnidentifiedImageError

from config import DB_PATH, RUNTIME_ATHLETE_IMAGES_DIR

# ─── R2 config ────────────────────────────────────────────────────────────────
ACCOUNT_ID = "f61c89ac113621bb9826f323f757966b"
BUCKET     = "ptd-static-assets"
R2_PREFIX  = "athlete_imgs"
ENDPOINT   = f"https://{ACCOUNT_ID}.r2.cloudflarestorage.com"
CACHE_CTRL = "public, max-age=31536000, immutable"

# ─── Local layout ─────────────────────────────────────────────────────────────
#   data/athlete_imgs/
#     raw/       ← source JPEGs ({athlete_id}.jpg), kept permanently
#     128/       ← 128×128 WebPs (card/list views)
#     512/       ← 512×512 WebPs (athlete hero, retina-ready)
SIZES   = (128, 512)
RAW_DIR = RUNTIME_ATHLETE_IMAGES_DIR / "raw"

# Load Haar cascade once at module level (ships with opencv-python-headless)
_CASCADE_PATH = cv2.data.haarcascades + "haarcascade_frontalface_default.xml"
_FACE_CASCADE = cv2.CascadeClassifier(_CASCADE_PATH)


# ─── Image processing ─────────────────────────────────────────────────────────

def face_crop_square(image: Image.Image) -> Image.Image:
    """Return the largest square crop centred on the biggest detected face.

    Falls back to top-centre crop (original behaviour) when no face is found.
    """
    w, h = image.size
    s = min(w, h)

    gray  = np.array(image.convert("L"))
    faces = _FACE_CASCADE.detectMultiScale(
        gray, scaleFactor=1.1, minNeighbors=5, minSize=(20, 20)
    )

    if len(faces) > 0:
        fx, fy, fw, fh = max(faces, key=lambda f: f[2] * f[3])
        cx = fx + fw // 2
        cy = fy + fh // 2
        # Centre the s×s crop on (cx, cy), clamped so the crop stays in-bounds
        left = max(0, min(cx - s // 2, w - s))
        top  = max(0, min(cy - s // 2, h - s))
        return image.crop((left, top, left + s, top + s))

    # Fallback: top-centre crop
    left = (w - s) // 2
    return image.crop((left, 0, left + s, s))


# ─── R2 helpers ───────────────────────────────────────────────────────────────

def make_s3():
    return boto3.client(
        "s3",
        endpoint_url=ENDPOINT,
        config=Config(
            region_name="auto",
            retries={"max_attempts": 10, "mode": "standard"},
        ),
    )


def list_r2_keys(s3, prefix: str) -> set[str]:
    keys = set()
    paginator = s3.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=BUCKET, Prefix=prefix):
        for obj in page.get("Contents", []):
            keys.add(obj["Key"])
    return keys


def clear_r2_prefix(s3, prefix: str) -> None:
    print(f"Clearing R2 {BUCKET}/{prefix}/...")
    paginator = s3.get_paginator("list_objects_v2")
    to_delete: list[dict] = []

    for page in paginator.paginate(Bucket=BUCKET, Prefix=f"{prefix}/"):
        for obj in page.get("Contents", []):
            to_delete.append({"Key": obj["Key"]})
            if len(to_delete) == 1000:
                s3.delete_objects(Bucket=BUCKET, Delete={"Objects": to_delete})
                to_delete.clear()

    if to_delete:
        s3.delete_objects(Bucket=BUCKET, Delete={"Objects": to_delete})
    print(f"  ✔ Cleared {prefix}/")


# ─── Pipeline steps ───────────────────────────────────────────────────────────

def migrate_raw_jpgs() -> None:
    """Move legacy *.jpg files from the root athlete_imgs dir into raw/."""
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    moved = 0
    for jpg in RUNTIME_ATHLETE_IMAGES_DIR.glob("*.jpg"):
        dest = RAW_DIR / jpg.name
        if not dest.exists():
            jpg.rename(dest)
            moved += 1
    if moved:
        print(f"Migrated {moved} raw JPGs to raw/")


def download_images(athletes: list[tuple[int, str]]) -> None:
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    downloaded = 0
    for athlete_id, url in athletes:
        dest = RAW_DIR / f"{athlete_id}.jpg"
        if dest.exists():
            continue
        try:
            r = requests.get(url, timeout=15)
            r.raise_for_status()
            dest.write_bytes(r.content)
            downloaded += 1
        except requests.RequestException as e:
            print(f"  Warning: download failed for athlete {athlete_id}: {e}")
    print(f"Downloaded {downloaded} new raw images.")


def process_images() -> None:
    processed = 0
    for jpg in sorted(RAW_DIR.glob("*.jpg")):
        outputs = {
            size: RUNTIME_ATHLETE_IMAGES_DIR / str(size) / f"{jpg.stem}.webp"
            for size in SIZES
        }
        if all(p.exists() for p in outputs.values()):
            continue
        try:
            with Image.open(jpg) as img:
                img.load()
                square = face_crop_square(img)
                for size, out_path in outputs.items():
                    if out_path.exists():
                        continue
                    out_path.parent.mkdir(parents=True, exist_ok=True)
                    square.resize((size, size), Image.LANCZOS).save(
                        out_path, format="WEBP", quality=90, method=6
                    )
            processed += 1
        except UnidentifiedImageError:
            print(f"  Warning: cannot identify image {jpg.name}")
    print(f"Processed {processed} images.")


def upload_images(s3, existing_keys: set[str]) -> None:
    uploaded = 0
    for size in SIZES:
        size_dir = RUNTIME_ATHLETE_IMAGES_DIR / str(size)
        if not size_dir.exists():
            continue
        for webp in sorted(size_dir.glob("*.webp")):
            key = f"{R2_PREFIX}/{size}/{webp.name}"
            if key in existing_keys:
                continue
            s3.upload_file(
                Filename=str(webp),
                Bucket=BUCKET,
                Key=key,
                ExtraArgs={"CacheControl": CACHE_CTRL, "ContentType": "image/webp"},
            )
            uploaded += 1
            if uploaded % 200 == 0:
                print(f"  {uploaded} uploaded...")
    print(f"Uploaded {uploaded} new files to R2.")


# ─── Entrypoint ───────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="Sync athlete profile images to R2.")
    parser.add_argument(
        "--clear", action="store_true",
        help="Delete R2 prefix and local WebPs, then do a full reprocess from raw."
    )
    args = parser.parse_args()

    if not RUNTIME_ATHLETE_IMAGES_DIR.exists():
        raise SystemExit(f"Image dir does not exist: {RUNTIME_ATHLETE_IMAGES_DIR}")

    # Move any legacy root-level JPGs into raw/
    migrate_raw_jpgs()

    # Query DB for all athletes that have a profile image URL
    conn = duckdb.connect(str(DB_PATH), read_only=True)
    rows = conn.execute(
        "SELECT athlete_id, profile_img FROM athletes WHERE profile_img != ''"
    ).fetchall()
    conn.close()
    athletes = [(row[0], row[1]) for row in rows]
    print(f"Found {len(athletes)} athletes with profile images.")

    s3 = make_s3()

    if args.clear:
        clear_r2_prefix(s3, R2_PREFIX)
        for size in SIZES:
            size_dir = RUNTIME_ATHLETE_IMAGES_DIR / str(size)
            if size_dir.exists():
                shutil.rmtree(size_dir)
                print(f"  Deleted local {size}/")

    download_images(athletes)
    process_images()

    print("Listing existing R2 keys (for incremental upload)...")
    existing = list_r2_keys(s3, R2_PREFIX)
    print(f"  {len(existing)} objects already in bucket.")
    upload_images(s3, existing)


if __name__ == "__main__":
    main()
