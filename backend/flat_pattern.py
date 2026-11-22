from time import perf_counter

import cv2
import numpy as np
from skimage.color import deltaE_ciede2000, rgb2lab

from backend.palette import load_palette


def generate_flat(image, size, color_limit):
    """Sample flat artwork, keeping transparent areas empty and dark drawn edges visible."""
    start = perf_counter()
    rgba = np.asarray(image)
    foreground = (rgba[:, :, 3] >= 128).astype(np.uint8)
    radius = max(1, round(image.width / 200))
    kernel = np.ones((2 * radius + 1, 2 * radius + 1), dtype=np.uint8)
    interior = cv2.erode(foreground, kernel, borderType=cv2.BORDER_CONSTANT, borderValue=0)
    edge = (foreground != 0) & (interior == 0)
    rgb = rgba[:, :, :3]
    luminance = np.einsum("ijk,k->ij", rgb, [0.2126, 0.7152, 0.0722])
    dark_edge = edge & (luminance < 115)
    bounds = np.rint(np.linspace(0, image.width, size + 1)).astype(int)
    samples = np.zeros((size, size, 3), dtype=np.uint8)
    occupied = np.zeros((size, size), dtype=bool)

    for row in range(size):
        for col in range(size):
            cell = np.s_[bounds[row]:bounds[row + 1], bounds[col]:bounds[col + 1]]
            mask = foreground[cell] != 0
            if mask.mean() < 0.5:
                continue
            occupied[row, col] = True
            cell_rgb = rgb[cell]
            dark = dark_edge[cell]
            if not mask.all() and dark.sum() >= max(6, edge[cell].sum() / 2):
                sample = np.median(cell_rgb[dark], axis=0)
            else:
                visible = cell_rgb[mask]
                bins = visible.astype(np.uint16) >> 4
                keys = (bins[:, 0] << 8) | (bins[:, 1] << 4) | bins[:, 2]
                sample = visible[keys == np.bincount(keys).argmax()].mean(axis=0)
            samples[row, col] = np.rint(sample)

    if not occupied.any():
        raise ValueError('底稿在目标尺寸下没有可用色块')

    palette = load_palette()
    card_rgb = palette[['r', 'g', 'b']].to_numpy(dtype=np.uint8)
    distances = deltaE_ciede2000(
        rgb2lab(samples[occupied] / 255.0)[:, None, :],
        rgb2lab(card_rgb / 255.0)[None, :, :],
    )
    closest = distances.argmin(axis=1)
    used, frequency = np.unique(closest, return_counts=True)
    if color_limit is not None and used.size > color_limit:
        allowed = used[np.argsort(-frequency, kind='stable')[:color_limit]]
        closest = allowed[distances[:, allowed].argmin(axis=1)]

    ids = np.full((size, size), None, dtype=object)
    ids[occupied] = palette.id.to_numpy()[closest]
    mapped_rgb = np.full_like(samples, 255)
    mapped_rgb[occupied] = card_rgb[closest]
    metrics = {
        'mean_delta_e2000': float(distances[np.arange(closest.size), closest].mean()),
        'colors': int(np.unique(closest).size),
        'beads': int(occupied.sum()),
        'processing_ms': (perf_counter() - start) * 1000,
    }
    return ids, mapped_rgb, samples, metrics
