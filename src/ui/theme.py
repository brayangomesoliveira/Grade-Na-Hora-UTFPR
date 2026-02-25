from __future__ import annotations

from PIL import Image, ImageTk

PAD, GAP, RADIUS = 16, 10, 10
TOKENS = {
    "dark": {"bg":"#0A666A","surface":"#1E777B","surface_2":"#2A8387","text":"#F5FAFB","muted":"#CFE8EA","line":"#0F8C90","border":"#4AA9AD","primary":"#4A9EA4","primary_hover":"#5EB0B5","secondary":"#2F7F84","secondary_hover":"#3D9095","danger":"#C94C4C","grid_fill":"#E8EFF8"},
    "light": {"bg":"#EAF3F4","surface":"#FFFFFF","surface_2":"#F7FBFB","text":"#123336","muted":"#3B676A","line":"#7FD2D5","border":"#B7DCDD","primary":"#0F7F84","primary_hover":"#146E72","secondary":"#D7ECEE","secondary_hover":"#C7E2E4","danger":"#C94C4C","grid_fill":"#EFF4FB"},
}
_CACHE: dict[tuple[int, int, str, str], ImageTk.PhotoImage] = {}


def gradient_photo(width: int, height: int, c1: str, c2: str) -> ImageTk.PhotoImage:
    key = (width, height, c1, c2)
    if key in _CACHE:
        return _CACHE[key]
    img = Image.new("RGB", (max(1, width), max(1, height)), c1)
    a, b = (tuple(int(c[i:i+2], 16) for i in (1, 3, 5)) for c in (c1, c2))
    px = img.load()
    for x in range(img.width):
        r = x / max(1, img.width - 1)
        cor = tuple(int(a[i] + (b[i] - a[i]) * r) for i in range(3))
        for y in range(img.height):
            px[x, y] = cor
    _CACHE[key] = ImageTk.PhotoImage(img)
    return _CACHE[key]