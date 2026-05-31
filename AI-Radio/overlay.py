from PIL import Image, ImageDraw, ImageFont
import socket

contributors = [
    "Damon Berry", "Paula Kelly", "Keith Colton", "Shane Ormande",
    "Richard Hayes", "Frank Duignan", "Mayank Parmar", "David Powell", "Shannon Chance", "  + Many More..."
]

# --- 1. Get Raspberry Pi's IP address ---
def get_ip():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        # Doesn't need to be reachable
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
    except:
        ip = "0.0.0.0"
    finally:
        s.close()
    return ip

def GenOverlay(dir, input_name, output_name):

    ip_address = get_ip()
    print("IP:", ip_address)

    fname_in = dir+"/"+input_name
    fname_out = dir+"/"+output_name

	# --- 2. Load or create a bitmap ---
	# Load existing bitmap:
    try:
        img = Image.open(fname_in)
    
    except:
        print(f"file not found... {fname_in}")
        return

	# Or create new blank bitmap:
	# img = Image.new("RGB", (800, 600), color="black")

    draw = ImageDraw.Draw(img)

	# --- 3. Choose font ---
	# Pillow on a Raspberry Pi usually has DejaVu fonts available:
    try:
        font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 22)
    except:
        font = ImageFont.load_default()

    # --- 4. Draw IP at coordinates (24, 24) ---
    
    # --- Box coordinates ---
    box_x1, box_y1 = 190, 30
    box_x2, box_y2 = 600, 200

    draw.rectangle((box_x1, box_y1, box_x2, box_y2), outline=(0, 0, 0), width=3)
    draw.text((box_x1+10, box_y2), ip_address, font=font, fill=(0, 0, 0))

    row_spacing = 25        # vertical spacing between names
    col_spacing = 200        # width of each column
    start_x = box_x1 + 10   # padding inside box
    start_y = box_y1 + 40

    draw.text(( start_x, box_y1+10), "Contributions From:", font=font, fill=(0,0,0))

    for index, name in enumerate(contributors):
        col = index % 2               # 0 = left column, 1 = right column
        row = index // 2              # row number
    
        x = start_x + col * col_spacing
        y = start_y + row * row_spacing
    
        # Avoid drawing outside box (optional safety check)
        if y < box_y2 - 5:
            draw.text((x, y), name, font=font, fill=(0,0,0))





    # --- 5. Save bitmap ---
    img.save(fname_out)
    print("Saved output.bmp")

if __name__ == "__main__":
    GenOverlay("/home/pi/vradio/output","images/vradio_banner.bmp", "images/vradio_banner_live.bmp")
