# 生成世界全景用的圆形角色令牌：img/avatar-XX.webp -> img/avatar-token-XX.webp
# 圆形透明底 + 角色本色描边环，贴在岛上不露黑方块。
import json, os
from PIL import Image, ImageDraw

chars = json.load(open("dist/characters.json", encoding="utf-8"))
S = 480
for c in chars:
    src = Image.open(c["avatar"]).convert("RGBA")
    canvas = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    # 填充式裁剪：短边撑满 480，长边居中裁掉，圆里不留黑边
    scale = S / min(src.width, src.height)
    src = src.resize((int(src.width * scale), int(src.height * scale)))
    ox = (src.width - S) // 2
    oy = (src.height - S) // 2
    canvas.alpha_composite(src, (-ox, -oy))
    mask = Image.new("L", (S, S), 0)
    d = ImageDraw.Draw(mask)
    d.ellipse([10, 10, S - 10, S - 10], fill=255)
    out = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    out.paste(canvas, (0, 0), mask)
    ring = ImageDraw.Draw(out)
    col = c.get("color", "#e9b44c")
    ring.ellipse([10, 10, S - 10, S - 10], outline=col, width=16)
    ring.ellipse([30, 30, S - 30, S - 30], outline=(30, 24, 18, 255), width=5)
    out.save(c["avatar"].replace("avatar-", "avatar-token-"), "WEBP", quality=88)
print("tokens:", len(chars))
