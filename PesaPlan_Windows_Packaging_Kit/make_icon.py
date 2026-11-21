from pathlib import Path
from PIL import Image

source = Path(__file__).with_name("icon.png")
target = Path(__file__).with_name("icon.ico")
if not source.is_file():
    raise SystemExit("icon.png is missing. Put this script beside your PesaPlan icon.")

image = Image.open(source).convert("RGBA")
image.save(target, format="ICO", sizes=[
    (16, 16), (24, 24), (32, 32), (48, 48),
    (64, 64), (128, 128), (256, 256)
])
print(f"Created {target}")
