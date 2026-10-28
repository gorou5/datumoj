# 把三视图拆成正/侧/背三张单人立绘：黑底按亮度转透明（世界场景里当站牌用）
# 用法: python3 make_views.py [id ...]（缺省全部）
import sys, os
import numpy as np
from PIL import Image

IDS = [int(x) for x in sys.argv[1:]] or list(range(1, 70))
NAMES = ["front", "side", "back"]

for cid in IDS:
    path = f"img/char-{cid:02d}.webp"
    im = Image.open(path).convert("RGB")
    a = np.asarray(im).astype(np.int32)
    lum = a.max(axis=2)
    mask = lum > 22
    colsum = mask.sum(axis=0)
    # 找被占据的列段（段间空隙 >= 8 列）
    runs, start = [], None
    for x in range(len(colsum)):
        if colsum[x] > 2 and start is None:
            start = x
        elif colsum[x] <= 2 and start is not None:
            if x - start > 25:
                runs.append((start, x))
            start = None
    if start is not None:
        runs.append((start, len(colsum)))
    if len(runs) != 3:
        W = im.width
        runs = [(0, W // 3), (W // 3, 2 * W // 3), (2 * W // 3, W)]
    for name, (x0, x1) in zip(NAMES, runs):
        sub_mask = mask[:, x0:x1]
        rows = np.where(sub_mask.any(axis=1))[0]
        cols = np.where(sub_mask.any(axis=0))[0]
        y0, y1 = rows.min(), rows.max() + 1
        cx0, cx1 = x0 + cols.min(), x0 + cols.max() + 1
        pad = 6
        cx0 = max(0, cx0 - pad); cx1 = min(im.width, cx1 + pad)
        y0 = max(0, y0 - pad); y1 = min(im.height, y1 + pad)
        crop = im.crop((cx0, y0, cx1, y1))
        cl = np.asarray(crop).astype(np.float32).max(axis=2)
        alpha = np.clip((cl - 13) / 42.0, 0, 1)
        alpha[cl <= 13] = 0
        # 连通域清理：只留最大主体（及 >8% 的附属件如手持道具），去掉漂移碎片
        from scipy import ndimage
        lab, n = ndimage.label(alpha > 0.45)
        if n > 1:
            sizes = ndimage.sum(alpha > 0.45, lab, range(1, n + 1))
            keep = np.isin(lab, [i + 1 for i, sz in enumerate(sizes) if sz >= sizes.max() * 0.08])
            alpha = alpha * keep
        rgba = np.dstack([np.asarray(crop), (alpha * 255).astype(np.uint8)])
        out = Image.fromarray(rgba, "RGBA")
        if out.height > 640:
            out = out.resize((int(out.width * 640 / out.height), 640))
        out.save(f"img/view-{name}-{cid:02d}.webp", "WEBP", quality=88)
print("views done:", len(IDS) * 3)
