from PIL import Image

img = Image.open("assets/necdetsanli.png").convert("RGBA")
out = []
for r, g, b, a in img.getdata():
    # near-white => transparent (eşik ayarlanabilir)
    if r > 245 and g > 245 and b > 245:
        out.append((r, g, b, 0))
    else:
        out.append((r, g, b, a))

img.putdata(out)
img.save("ascii_transparent.png")
