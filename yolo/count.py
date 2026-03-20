from PIL import Image
import os

img_dir = r"D:\dataset\images"

bad = 0

for root, dirs, files in os.walk(img_dir):
    for file in files:
        if file.endswith((".png", ".jpg", ".jpeg")):
            path = os.path.join(root, file)

            try:
                with Image.open(path) as img:
                    img.verify()
            except:
                print("坏图:", path)
                bad += 1

print("坏图数量:", bad)