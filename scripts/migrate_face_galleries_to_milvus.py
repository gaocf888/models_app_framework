#!/usr/bin/env python3
"""将 data/face_galleries 下 JSON 中的 embedding 全量导入 Milvus。"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# 项目根目录
_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))


def main() -> int:
    parser = argparse.ArgumentParser(description="Migrate face gallery JSON embeddings to Milvus")
    parser.add_argument(
        "--base-dir",
        default="data/face_galleries",
        help="face galleries root (default: data/face_galleries)",
    )
    parser.add_argument("--gallery-id", help="only migrate one gallery")
    parser.add_argument("--dry-run", action="store_true", help="list galleries without writing")
    args = parser.parse_args()

    from app.small_models.strategy.face.vector_config import get_face_vector_config, use_milvus_backend

    if not use_milvus_backend():
        print("ERROR: set FACE_VECTOR_BACKEND=milvus before migration", file=sys.stderr)
        return 1

    cfg = get_face_vector_config()
    print(f"milvus uri={cfg.milvus_uri} collection={cfg.milvus_collection} dim={cfg.embedding_dim}")

    from app.small_models.strategy.face.gallery_store import FaceGalleryStore

    store = FaceGalleryStore(base_dir=args.base_dir)
    galleries = store.list_galleries()
    if args.gallery_id:
        galleries = [g for g in galleries if g["gallery_id"] == args.gallery_id]
        if not galleries:
            print(f"gallery not found: {args.gallery_id}", file=sys.stderr)
            return 1

    if not galleries:
        print("no galleries found")
        return 0

    total = 0
    for meta in galleries:
        gid = meta["gallery_id"]
        count = meta.get("sample_count", 0)
        print(f"- {gid}: {count} samples")
        if args.dry_run:
            total += count
            continue
        result = store.sync_gallery_vectors(gid)
        synced = int(result["synced_samples"])
        total += synced
        print(f"  synced {synced}")

    print(f"done: {len(galleries)} gallery(ies), {total} sample(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
