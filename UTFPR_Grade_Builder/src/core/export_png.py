from __future__ import annotations

import hashlib
from datetime import datetime
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from .models import ScheduleBuildResult, Turma
from .schedule import DIA_LABELS_LONG

PNG_THEME = {
    "dark": {
        "bg": "#0B0F14",
        "surface": "#111827",
        "header": "#1D4ED8",
        "text": "#F3F6FB",
        "grid_bg": "#EAF0F7",
        "grid_line": "#B7C8DA",
        "conflict": "#E53935",
    },
    "light": {
        "bg": "#EEF2F7",
        "surface": "#FFFFFF",
        "header": "#2563EB",
        "text": "#111827",
        "grid_bg": "#F7FAFD",
        "grid_line": "#C8D5E3",
        "conflict": "#E53935",
    },
}


def default_png_name() -> str:
    return f"grade_utfpr_{datetime.now().strftime('%Y-%m-%d_%H%M')}.png"


def _load_font(size: int, bold: bool = False) -> ImageFont.ImageFont:
    candidatos = (
        ["segoeuib.ttf", "arialbd.ttf", "DejaVuSans-Bold.ttf", "LiberationSans-Bold.ttf"]
        if bold
        else ["segoeui.ttf", "arial.ttf", "DejaVuSans.ttf", "LiberationSans-Regular.ttf"]
    )
    for nome in candidatos:
        try:
            return ImageFont.truetype(nome, size=size)
        except OSError:
            continue
    return ImageFont.load_default()


def _hash_color(texto: str) -> str:
    md5 = hashlib.md5(texto.encode("utf-8")).hexdigest()
    rgb = [int(md5[i : i + 2], 16) for i in (0, 2, 4)]
    rgb = [min(220, max(70, v)) for v in rgb]
    return f"#{rgb[0]:02X}{rgb[1]:02X}{rgb[2]:02X}"


def _cell_text(cell_turmas: list[Turma]) -> str:
    if not cell_turmas:
        return ""
    if len(cell_turmas) == 1:
        t = cell_turmas[0]
        room = next((h.room for h in t.horarios if h.room), None)
        return f"{t.disciplina_codigo} - {t.turma_codigo}" + (f"\n{room}" if room else "")
    return "\n".join(f"{t.disciplina_codigo} - {t.turma_codigo}" for t in cell_turmas[:3])


def _wrap(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.ImageFont, max_w: int) -> str:
    palavras = text.split()
    if not palavras:
        return text
    linhas: list[str] = []
    atual = palavras[0]
    for palavra in palavras[1:]:
        teste = f"{atual} {palavra}"
        bbox = draw.textbbox((0, 0), teste, font=font)
        if bbox[2] - bbox[0] <= max_w:
            atual = teste
        else:
            linhas.append(atual)
            atual = palavra
    linhas.append(atual)
    return "\n".join(linhas)


def export_schedule_png(
    result: ScheduleBuildResult,
    output_path: str | Path,
    *,
    title: str = "Grade UTFPR",
    subtitle: str | None = None,
    theme: str = "dark",
    width: int = 2400,
    height: int = 1400,
) -> Path:
    """Exporta grade para PNG a partir do modelo de dados (sem screenshot)."""
    colors = PNG_THEME.get(theme, PNG_THEME["dark"])
    out_path = Path(output_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    img = Image.new("RGB", (width, height), colors["bg"])
    draw = ImageDraw.Draw(img)

    ft_title = _load_font(42, bold=True)
    ft_sub = _load_font(20)
    ft_header = _load_font(20, bold=True)
    ft_row = _load_font(18, bold=True)
    ft_cell = _load_font(16, bold=True)
    ft_cell_small = _load_font(14)

    margin = 40
    top_h = 120
    left_w = 120
    days = [2, 3, 4, 5, 6, 7]
    row_labels = [*(f"M{i}" for i in range(1, 7)), *(f"T{i}" for i in range(1, 7)), *(f"N{i}" for i in range(1, 6))]
    grid_x = margin
    grid_y = margin + top_h
    grid_w = width - 2 * margin
    grid_h = height - grid_y - margin
    head_h = 42
    cell_w = (grid_w - left_w) / len(days)
    cell_h = (grid_h - head_h) / len(row_labels)

    draw.rounded_rectangle(
        (margin, margin, width - margin, margin + 70),
        radius=16,
        fill=colors["surface"],
    )
    draw.text((margin + 18, margin + 12), title, fill=colors["text"], font=ft_title)
    sub = subtitle or datetime.now().strftime("Gerado em %Y-%m-%d %H:%M")
    draw.text((margin + 20, margin + 76), sub, fill=colors["text"], font=ft_sub)

    for i, day in enumerate(days):
        x1 = grid_x + left_w + i * cell_w
        x2 = x1 + cell_w
        draw.rectangle((x1, grid_y, x2, grid_y + head_h), fill=colors["header"])
        lbl = DIA_LABELS_LONG[day]
        bbox = draw.textbbox((0, 0), lbl, font=ft_header)
        draw.text(((x1 + x2 - (bbox[2] - bbox[0])) / 2, grid_y + 10), lbl, fill="#FFFFFF", font=ft_header)

    for row_idx, lbl in enumerate(row_labels):
        y1 = grid_y + head_h + row_idx * cell_h
        y2 = y1 + cell_h
        draw.rectangle((grid_x, y1, grid_x + left_w, y2), fill=colors["header"])
        bbox = draw.textbbox((0, 0), lbl, font=ft_row)
        draw.text(
            (grid_x + (left_w - (bbox[2] - bbox[0])) / 2, y1 + (cell_h - (bbox[3] - bbox[1])) / 2),
            lbl,
            fill="#FFFFFF",
            font=ft_row,
        )
        period = lbl[0]
        slot_num = int(lbl[1:])
        for day_idx in range(6):
            x1 = grid_x + left_w + day_idx * cell_w
            x2 = x1 + cell_w
            draw.rectangle((x1, y1, x2, y2), fill=colors["grid_bg"], outline=colors["grid_line"], width=1)
            turmas = result.grid.get(period, {}).get(slot_num, {}).get(day_idx, [])
            if not turmas:
                continue
            conflict = len({t.uid() for t in turmas}) > 1
            fill = _hash_color(turmas[0].uid())
            draw.rounded_rectangle(
                (x1 + 2, y1 + 2, x2 - 2, y2 - 2),
                radius=6,
                fill=fill,
                outline=colors["conflict"] if conflict else fill,
                width=3 if conflict else 1,
            )
            txt = _cell_text(turmas)
            wrapped = _wrap(draw, txt, ft_cell, int(cell_w - 12))
            fnt = ft_cell_small if "\n" in wrapped else ft_cell
            draw.multiline_text((x1 + 6, y1 + 4), wrapped, fill="#FFFFFF", font=fnt, spacing=2)

    img.save(out_path, format="PNG")
    return out_path
