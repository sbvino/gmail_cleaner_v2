# Favicon Instructions

To add a favicon to your Gmail AI Cleaner application:

## Option 1: Create a simple favicon
```bash
# Install ImageMagick if not already installed
sudo apt-get install imagemagick

# Create a simple favicon with Gmail-like colors
convert -size 32x32 xc:'#4285f4' \
  -draw "fill white circle 16,16 16,8" \
  -draw "fill #4285f4 circle 16,16 16,12" \
  static/favicon.ico
```

## Option 2: Use an online generator
1. Visit https://favicon.io/
2. Create or upload an icon
3. Download and save as `static/favicon.ico`

## Option 3: Use Font Awesome
```bash
# Download Font Awesome envelope icon as favicon
wget -O static/favicon.ico https://raw.githubusercontent.com/favicon.io/favicon.io/master/assets/favicon/favicon.ico
```

## Option 4: Create programmatically
Create a file `create_favicon.py`:
```python
from PIL import Image, ImageDraw

# Create a 32x32 image with Gmail-like colors
img = Image.new('RGB', (32, 32), color='#4285f4')
draw = ImageDraw.Draw(img)

# Draw an envelope shape
draw.polygon([(4, 10), (28, 10), (16, 20)], fill='white')
draw.rectangle([4, 10, 28, 26], fill='white')

# Save as ICO
img.save('static/favicon.ico', format='ICO', sizes=[(32, 32)])
```

The favicon is optional but recommended for a professional appearance.
