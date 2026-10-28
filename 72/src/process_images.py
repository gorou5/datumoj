# -*- coding: utf-8 -*-
"""把 gen/ 里的三视图原图处理成 img/char-XX.webp（全图）与 img/avatar-XX.webp（头部大头照）。
大头照 = 正面视图（左 1/3）的头部区域：x 1%–34.5%、y 0–50%，头部＋肩部构图。"""
import glob, os, re, sys
from PIL import Image

def main(ids):
    for i in ids:
        pats = sorted(glob.glob(f"gen/media-generation-char-{i:02d}-*.webp"))
        if not pats:
            print(i, "MISSING"); continue
        im = Image.open(pats[-1]).convert("RGB")
        W, H = im.size
        im.save(f"img/char-{i:02d}.webp", quality=88)
        head = im.crop((int(W*0.01), 0, int(W*0.345), int(H*0.50)))
        head.thumbnail((480, 480))
        head.save(f"img/avatar-{i:02d}.webp", quality=88)
        print(i, "ok", head.size)

if __name__ == "__main__":
    ids = [int(x) for x in sys.argv[1:]] or list(range(1, 70))
    main(ids)
