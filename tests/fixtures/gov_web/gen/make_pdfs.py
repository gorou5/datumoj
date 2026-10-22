"""生成合成 PDF 样本：scanned.pdf（只有图像、没有文本层）和 encrypted.pdf（标准安全处理器 RC4 40 位，需要用户密码）。
用法：python make_pdfs.py <输出目录>。只用标准库和 Pillow。"""
import hashlib, io, sys, zlib
from pathlib import Path
from PIL import Image, ImageDraw

out = Path(sys.argv[1])

# ---- scanned.pdf：两页纯图像（模拟扫描件），没有任何文字对象 ----
pages = []
for n in (1, 2):
    img = Image.new("L", (850, 1100), 245)
    d = ImageDraw.Draw(img)
    for y in range(120, 1000, 28):  # 画出“文字行”的灰条，模拟扫描的正文
        d.rectangle([90, y, 90 + (600 if (y // 28) % 5 else 380), y + 12], fill=90)
    d.text((90, 60), f"SCANNED PAGE {n}", fill=0)
    pages.append(img)
pages[0].save(out / "scanned.pdf", save_all=True, append_images=pages[1:], resolution=100)

# ---- encrypted.pdf：一页，带 /Encrypt（V1 R2，40 位 RC4），用户密码 datumoj-test ----
PAD = bytes.fromhex("28BF4E5E4E758A4164004E56FFFA01082E2E00B6D0683E802F0CA9FE6453697A")

def rc4(key, data):
    s = list(range(256)); j = 0
    for i in range(256):
        j = (j + s[i] + key[i % len(key)]) % 256; s[i], s[j] = s[j], s[i]
    i = j = 0; res = bytearray()
    for b in data:
        i = (i + 1) % 256; j = (j + s[i]) % 256; s[i], s[j] = s[j], s[i]
        res.append(b ^ s[(s[i] + s[j]) % 256])
    return bytes(res)

def padpw(pw): return (pw.encode() + PAD)[:32]

user_pw, owner_pw = "datumoj-test", "datumoj-owner"
P = -44  # 允许打印等的常见值
file_id = hashlib.md5(b"datumoj synthetic encrypted fixture").digest()
O = rc4(hashlib.md5(padpw(owner_pw)).digest()[:5], padpw(user_pw))
key = hashlib.md5(padpw(user_pw) + O + P.to_bytes(4, "little", signed=True) + file_id).digest()[:5]
U = rc4(key, PAD)

def obj_key(num, gen=0):
    return hashlib.md5(key + num.to_bytes(3, "little") + gen.to_bytes(2, "little")).digest()[:10]

content = b"BT /F1 18 Tf 72 720 Td (DATUMOJ encrypted test fixture) Tj ET"
enc_content = rc4(obj_key(4), content)
hexs = lambda b: "<" + b.hex() + ">"
objs = [
    b"<< /Type /Catalog /Pages 2 0 R >>",
    b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
    b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>",
    b"<< /Length %d >>\nstream\n" % len(enc_content) + enc_content + b"\nendstream",
    b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ("<< /Filter /Standard /V 1 /R 2 /O %s /U %s /P %d >>" % (hexs(O), hexs(U), P)).encode(),
]
buf = io.BytesIO(); buf.write(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n"); offsets = []
for i, body in enumerate(objs, 1):
    offsets.append(buf.tell()); buf.write(b"%d 0 obj\n" % i + body + b"\nendobj\n")
xref = buf.tell()
buf.write(b"xref\n0 %d\n0000000000 65535 f \n" % (len(objs) + 1))
for off in offsets: buf.write(b"%010d 00000 n \n" % off)
buf.write(("trailer\n<< /Size %d /Root 1 0 R /Encrypt 6 0 R /ID [%s %s] >>\nstartxref\n%d\n%%%%EOF\n"
           % (len(objs) + 1, hexs(file_id), hexs(file_id), xref)).encode())
(out / "encrypted.pdf").write_bytes(buf.getvalue())
