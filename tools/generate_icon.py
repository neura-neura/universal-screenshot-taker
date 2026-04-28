from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter


def build_icon(size: int = 1024) -> Image.Image:
    image = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)

    # Background.
    draw.rounded_rectangle(
        (96, 96, size - 96, size - 96),
        radius=220,
        fill=(18, 43, 68, 255),
    )
    draw.rounded_rectangle(
        (132, 132, size - 132, size - 132),
        radius=190,
        fill=(28, 71, 104, 255),
    )

    # Glow accent.
    glow = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    glow_draw = ImageDraw.Draw(glow)
    glow_draw.ellipse((250, 170, 760, 680), fill=(96, 205, 255, 110))
    glow = glow.filter(ImageFilter.GaussianBlur(36))
    image.alpha_composite(glow)

    # Capture frame.
    frame_left, frame_top, frame_right, frame_bottom = 230, 240, 794, 688
    draw.rounded_rectangle(
        (frame_left, frame_top, frame_right, frame_bottom),
        radius=80,
        outline=(232, 246, 255, 255),
        width=34,
    )
    draw.line((frame_left + 120, frame_top, frame_left + 120, frame_top + 120), fill=(232, 246, 255, 255), width=34)
    draw.line((frame_left, frame_top + 120, frame_left + 120, frame_top + 120), fill=(232, 246, 255, 255), width=34)
    draw.line((frame_right - 120, frame_top, frame_right - 120, frame_top + 120), fill=(232, 246, 255, 255), width=34)
    draw.line((frame_right - 120, frame_top + 120, frame_right, frame_top + 120), fill=(232, 246, 255, 255), width=34)
    draw.line((frame_left + 120, frame_bottom - 120, frame_left + 120, frame_bottom), fill=(232, 246, 255, 255), width=34)
    draw.line((frame_left, frame_bottom - 120, frame_left + 120, frame_bottom - 120), fill=(232, 246, 255, 255), width=34)
    draw.line((frame_right - 120, frame_bottom - 120, frame_right - 120, frame_bottom), fill=(232, 246, 255, 255), width=34)
    draw.line((frame_right - 120, frame_bottom - 120, frame_right, frame_bottom - 120), fill=(232, 246, 255, 255), width=34)

    # Camera lens.
    draw.rounded_rectangle((382, 392, 642, 576), radius=68, fill=(242, 179, 54, 255))
    draw.ellipse((452, 420, 572, 540), fill=(255, 247, 214, 255))
    draw.ellipse((482, 450, 542, 510), fill=(28, 71, 104, 255))
    draw.rounded_rectangle((632, 420, 720, 482), radius=24, fill=(242, 179, 54, 255))

    # Page accent.
    draw.rounded_rectangle((314, 318, 486, 390), radius=26, fill=(96, 205, 255, 255))
    draw.rounded_rectangle((314, 610, 548, 656), radius=18, fill=(96, 205, 255, 255))

    return image


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    assets_dir = root / "assets"
    assets_dir.mkdir(parents=True, exist_ok=True)

    icon = build_icon()
    png_path = assets_dir / "app_icon.png"
    ico_path = assets_dir / "app_icon.ico"

    icon.save(png_path)
    icon.save(ico_path, sizes=[(256, 256), (128, 128), (64, 64), (48, 48), (32, 32), (16, 16)])


if __name__ == "__main__":
    main()
