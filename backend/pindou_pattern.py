"""Run the vendored PinDou converter using BeadStudio's MARD color card."""

import json
from pathlib import Path
import subprocess
from time import perf_counter

import cv2
import numpy as np
from skimage.color import deltaE_ciede2000, rgb2lab

from backend.detail_refine import refine_grid
from backend.palette import load_palette


CONVERTER = Path(__file__).parent / 'vendor' / 'pindou' / 'convert.cjs'


def generate_pindou(image, size, color_limit, *, refine=False):
    start = perf_counter()
    colors = list(load_palette()[['id', 'r', 'g', 'b']].itertuples(index=False, name=None))
    metadata = json.dumps({
        'side': image.width, 'size': size, 'colorLimit': color_limit, 'colors': colors,
    }).encode('utf-8')
    try:
        result = subprocess.run(
            ['node', str(CONVERTER)],
            input=metadata + b'\n' + image.tobytes(),
            capture_output=True,
            check=True,
            timeout=30,
        )
    except FileNotFoundError as exc:
        raise RuntimeError('PinDou 需要 Node.js 18+，请确认 node 在 PATH 中') from exc
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError('PinDou 转换超时') from exc
    except subprocess.CalledProcessError as exc:
        raise RuntimeError('PinDou 无法转换此图片') from exc

    ids = np.asarray(json.loads(result.stdout), dtype=object).reshape(size, size)
    occupied = np.not_equal(ids, None)
    if not occupied.any():
        raise ValueError('底稿在目标尺寸下没有可用色块')

    by_id = {code: (r, g, b) for code, r, g, b in colors}
    source = np.asarray(image)
    refinement = {}
    if refine:
        original = ids
        ids, details = refine_grid(source, original, by_id, color_limit)
        refinement = {
            'refined_cells': int((ids != original).sum()),
            **{f'refine_{name}': value for name, value in details.items()},
        }

    rgb = np.full((size, size, 3), 255, dtype=np.uint8)
    rgb[occupied] = [by_id[code] for code in ids[occupied]]
    rgba = source.astype(np.float32) / 255
    alpha = rgba[:, :, 3]
    area_alpha = cv2.resize(alpha, (size, size), interpolation=cv2.INTER_AREA)
    premultiplied = cv2.resize(rgba[:, :, :3] * alpha[:, :, None], (size, size), interpolation=cv2.INTER_AREA)
    reference = premultiplied / np.maximum(area_alpha[:, :, None], 1e-6)
    error = deltaE_ciede2000(rgb2lab(reference[occupied]), rgb2lab(rgb[occupied] / 255.0))
    metrics = {
        'mean_delta_e2000': float(error.mean()),
        'colors': len(set(ids[occupied])),
        'beads': int(occupied.sum()),
        'processing_ms': (perf_counter() - start) * 1000,
        **refinement,
    }
    return ids, rgb, np.rint(reference * 255).astype(np.uint8), metrics
