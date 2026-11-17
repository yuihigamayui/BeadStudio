from io import BytesIO
from pathlib import Path
import numpy as np
import pandas as pd
from PIL import Image, ImageDraw, ImageFont


def usage_table(matrix, palette):
    ids, counts = np.unique(matrix, return_counts=True)
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
    width = max(w * cell + 40, 560)
    columns = max(1, width // 180)
    rows = (len(table) + columns - 1) // columns
    image = Image.new('RGB', (width, h * cell + 90 + rows * 32), 'white')
    draw = ImageDraw.Draw(image)
    label_font = font(max(10, cell // 3))
    for y in range(h):
        for x in range(w):
            code = matrix[y, x]
            entry = lookup.loc[code]
            color = tuple(int(entry[c]) for c in ('r', 'g', 'b'))
            left, top = 20 + x * cell, 20 + y * cell
            draw.rectangle((left, top, left+cell-1, top+cell-1), fill=color, outline='#8a8a8a' if show_grid else None)
            if show_ids:
                ink = 'black' if np.dot(color, [.2126, .7152, .0722]) > 145 else 'white'
                draw.text((left+cell/2, top+cell/2), code, anchor='mm', fill=ink, font=label_font)
    draw.text((20, h*cell+35), 'BeadStudio V3 | ID : bead count', fill='black', font=font(16))
    for i, row in enumerate(table.itertuples()):
        x, y = 20+(i % columns)*180, h*cell+65+(i//columns)*32
        draw.rectangle((x, y, x+20, y+20), fill=row.hex, outline='gray')
        draw.text((x+28, y+2), f'{row.id}: {row.count}', fill='black', font=font(15))
    return image


def png_bytes(image):
    buffer = BytesIO()
    image.save(buffer, format='PNG')
    return buffer.getvalue()


def matrix_csv(matrix):
    return pd.DataFrame(matrix).to_csv(index=False, header=False).encode('utf-8-sig')
