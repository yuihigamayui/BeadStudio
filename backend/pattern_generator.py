from io import BytesIO
from pathlib import Path
import numpy as np
import pandas as pd
from PIL import Image, ImageDraw, ImageFont


def usage_table(matrix, palette):
    matrix = np.asarray(matrix, dtype=object)
    ids, counts = np.unique(matrix[matrix != None], return_counts=True)
    table = pd.DataFrame({'id': ids, 'count': counts}).merge(palette, on='id', validate='one_to_one')
    table['ratio'] = table['count'] / np.asarray(matrix).size
    return table.sort_values(['count', 'id'], ascending=[False, True]).reset_index(drop=True)


def font(size):
    for path in ('C:/Windows/Fonts/msyh.ttc', 'C:/Windows/Fonts/arial.ttf', '/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf'):
        if Path(path).exists():
            return ImageFont.truetype(path, size)
    return ImageFont.load_default(size=size)


def render_pattern(matrix, palette, show_ids=True, show_grid=True, cell=36):
    table = usage_table(matrix, palette)
    lookup = palette.set_index('id')
    h, w = matrix.shape
    left_margin, top_margin = 36, 64
    width = max(w * cell + left_margin * 2, 560)
    columns = max(1, (width - left_margin * 2) // 160)
    rows = (len(table) + columns - 1) // columns
    grid_bottom = top_margin + h * cell
    image = Image.new('RGB', (width, grid_bottom + 65 + rows * 32), 'white')
    draw = ImageDraw.Draw(image)
    label_font = font(max(10, cell // 3))
    draw.text((left_margin, 14), f'BeadStudio | {w} x {h} | {len(table)} colors | {table["count"].sum()} beads',
              fill='black', font=font(18))
    for y, row in enumerate(matrix):
        top = top_margin + y * cell
        if show_grid:
            draw.text((left_margin - 15, top + cell / 2), str(y + 1), anchor='mm', fill='black', font=label_font)
        for x, code in enumerate(row):
            left = left_margin + x * cell
            if show_grid and y == 0:
                draw.text((left + cell / 2, top_margin - 15), str(x + 1), anchor='mm', fill='black', font=label_font)
            color = tuple(int(c) for c in lookup.loc[code, ['r', 'g', 'b']]) if code is not None else '#F5F5F5'
            draw.rectangle((left, top, left + cell - 1, top + cell - 1), fill=color,
                           outline='#969696' if show_grid else None)
            if show_ids and code is not None:
                ink = 'black' if np.dot(color, [.2126, .7152, .0722]) > 145 else 'white'
                draw.text((left + cell / 2, top + cell / 2), code, anchor='mm', fill=ink, font=label_font)
    draw.text((left_margin, grid_bottom + 18), 'Color code and bead count', fill='black', font=font(16))
    for i, row in enumerate(table.itertuples()):
        x = left_margin + (i % columns) * 160
        y = grid_bottom + 47 + (i // columns) * 32
        draw.rectangle((x, y, x+20, y+20), fill=row.hex, outline='gray')
        draw.text((x+28, y+2), f'{row.id}: {row.count}', fill='black', font=font(15))
    return image


def png_bytes(image):
    buffer = BytesIO()
    image.save(buffer, format='PNG')
    return buffer.getvalue()


def matrix_csv(matrix):
    return pd.DataFrame(matrix).to_csv(index=False, header=False).encode('utf-8-sig')
