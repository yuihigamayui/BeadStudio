"""Automatic, palette-bounded detail preservation for flat artwork."""
from itertools import product

import cv2
import numpy as np
from skimage.color import deltaE_ciede2000, rgb2lab

SUBCELLS = 12
SIDES = cv2.getStructuringElement(cv2.MORPH_CROSS, (3, 3))


def _project(mask, size):
    return cv2.resize(mask.astype(np.float32), (size, size), interpolation=cv2.INTER_AREA)


def _disk(radius):
    return cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * radius + 1, 2 * radius + 1))


def _dilate(mask, kernel):
    return cv2.dilate(mask.astype(np.uint8), kernel).astype(bool)


def _components(mask, connectivity=8):
    _, labels, stats, _ = cv2.connectedComponentsWithStats(mask.astype(np.uint8), connectivity=connectivity)
    return labels, stats


def _resample(rgba, shape):
    alpha = rgba[:, :, 3].astype(np.float32) / 255
    alpha_small = cv2.resize(alpha, shape, interpolation=cv2.INTER_AREA)
    premultiplied = rgba[:, :, :3] * alpha[:, :, None]
    sampled = cv2.resize(premultiplied, shape, interpolation=cv2.INTER_AREA)
    rgb = np.clip(sampled / np.maximum(alpha_small[:, :, None], 1e-6), 0, 255)
    return rgb, alpha_small >= .55


def _flatness(rgba):
    # Evaluate texture at a consistent source scale without padded blank areas.
    x, y, width, height = cv2.boundingRect((rgba[:, :, 3] >= 128).astype(np.uint8))
    if not width or not height:
        return 0.0
    patch = rgba[y:y + height, x:x + width]
    multiplier = min(1., 600 / max(patch.shape[:2]))
    shape = (max(1, round(patch.shape[1] * multiplier)), max(1, round(patch.shape[0] * multiplier)))
    rgb, mask = _resample(patch, shape)
    interior = cv2.erode(mask.astype(np.uint8), _disk(2)).astype(bool)
    if not interior.any():
        return 0.
    detail = np.max(np.abs(rgb-cv2.GaussianBlur(rgb, (0, 0), 1.6)), axis=2)
    return float(np.mean(detail[interior] <= 2))


def _source_palette(rgb, opaque, colors):
    # Match bucket averages once per occupied 5-bit color neighborhood.
    buckets = rgb[opaque].astype(np.int32) >> 3
    packed = (buckets[:, 0] << 10) | (buckets[:, 1] << 5) | buckets[:, 2]
    _, inverse = np.unique(packed, return_inverse=True)
    weights = np.bincount(inverse)
    means = np.stack([np.bincount(inverse, weights=c) / weights for c in rgb[opaque].T], axis=1)
    lab = rgb2lab(colors / 255.)
    dist = deltaE_ciede2000(rgb2lab(means / 255.)[:, None, :], lab[None, :, :])
    nearest = dist.argmin(axis=1)
    labels = np.full(opaque.shape, -1, dtype=np.int16)
    labels[opaque] = nearest[inverse]
    palette_distance = deltaE_ciede2000(lab[:, None, :], lab[None, :, :])
    return labels, palette_distance


def _ring_options(lens_cov, rim_cov, visible, anchor):
    options = []
    lens_area = lens_cov.sum()
    for threshold in (.56, .63, .70, .77, .84):
        core = lens_cov >= threshold
        if not core[anchor] or core.sum() < .68 * lens_area:
            continue
        if len(_components(core, 4)[1]) != 2:
            continue
        border = _dilate(core, SIDES) & ~core
        if (border & ~visible).any() or (core & ~visible).any():
            continue
        evidence = rim_cov[border]
        if (evidence >= .055).mean() < .66 or evidence.mean() < .12:
            continue
        lens_error = (lens_cov + core-2 * lens_cov * core).sum()
        missing_rim = rim_cov[~border].sum()
        unsupported_rim = (evidence < .055).sum()
        score = lens_error + 1.45 * missing_rim + .32 * unsupported_rim
        options.append((float(score), core, border))
    return sorted(options, key=lambda option:option[0])


def _preserve_enclosed_details(rgb, source, labels, distance, colors, grid):
    """Quantize supported highlights and closed outlines together."""
    size = len(grid)
    white = source & (rgb.min(axis=2) > 220) & (np.ptp(rgb, axis=2) < 25)
    dark = source & (rgb.max(axis=2) < 78)
    bright_labels, bright_stats = _components(white)
    dark_labels, dark_stats = _components(dark)
    occupied = grid >= 0
    seen = set()
    details = []
    for i, (_, _, _, _, area) in enumerate(bright_stats[1:], 1):
        if not .15 * SUBCELLS**2 <= area <= 2.2 * SUBCELLS**2:
            continue
        highlight = bright_labels == i
        surround = _dilate(highlight, _disk(6)) & ~highlight
        if dark[surround].mean() < .70:
            continue
        bright_code = int(np.argmax(np.bincount(labels[highlight])))
        if colors[bright_code].min() <= 210:
            continue
        overlap = _project(highlight, size)
        anchor = np.unravel_index(np.argmax(np.where(occupied, overlap, 0)), grid.shape)
        if overlap[anchor] < .15 or anchor in seen:
            continue
        part = int(np.bincount(dark_labels[surround & dark]).argmax())
        seen.add(anchor)
        rings = []
        dark_code = grid[anchor]
        if 4 * SUBCELLS**2 <= dark_stats[part, cv2.CC_STAT_AREA] <= 50 * SUBCELLS**2:
            dark_mask = (dark_labels == part).astype(np.uint8)
            contours, _ = cv2.findContours(dark_mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
            lens = cv2.drawContours(np.zeros_like(dark_mask), contours, -1, 1, cv2.FILLED).astype(bool)
            band = _dilate(lens, _disk(3)) & ~lens & source
            if band.any() and np.mean(lens[highlight]) >= .80:
                dark_code = int(np.argmax(np.bincount(labels[dark_mask.astype(bool)])))
                rim_code = int(np.argmax(np.bincount(labels[band])))
                if np.mean(labels[band] == rim_code) >= .50 and distance[dark_code, rim_code] >= 22:
                    wide_band = _dilate(lens, _disk(5)) & ~lens & source
                    close_colors = np.flatnonzero(distance[rim_code] <= 8)
                    ink = wide_band & np.isin(labels, close_colors)
                    rings = _ring_options(_project(lens, size), _project(ink, size), occupied, anchor)
        details.append((anchor, bright_code, dark_code, rim_code if rings else -1, rings))
    changed_rings = 0
    protected = np.zeros_like(occupied)
    anchors = np.zeros_like(occupied)
    for anchor, *_ in details:
        anchors[anchor] = True
    for _, _, lens_code, rim_code, options in details:
        for _, core, edge in options:
            if (protected & core).any() or (protected & edge & (grid != rim_code)).any():
                continue
            if (edge & anchors).any():
                continue
            grid[core] = lens_code
            grid[edge] = rim_code
            protected |= core | edge
            changed_rings += 1
            break
    for anchor, bright, *_ in details:
        grid[anchor] = bright
    return len(details), changed_rings


def _recover_local_color(labels, distance, colors, grid):
    # Strong, mostly enclosed patches sometimes lose a majority cell to
    # dark-line priority. Recover only the cells with positive area evidence
    # that touch another grid cell already representing the patch.
    size = len(grid)
    lightness = rgb2lab(colors / 255.)[:, 0]
    edits = 0
    for color in range(len(colors)):
        if lightness[color] < 36 or colors[color].min() > 210:
            continue
        regions, props = _components(labels == color)
        for ident, (_, _, w, h, area) in enumerate(props[1:], 1):
            if not 2.4 * SUBCELLS**2 <= area <= 8 * SUBCELLS**2 or max(w, h) > 5 * SUBCELLS:
                continue
            if area / (w * h) < .28:
                continue
            region = regions == ident
            edge = _dilate(region, _disk(5)) & ~region
            nearby = labels[edge]
            foreground = nearby >= 0
            if foreground.mean() < .9:
                continue
            ring = nearby[foreground]
            contrast = (distance[color, ring] >= 24) & (lightness[color]-lightness[ring] >= 14)
            if contrast.mean() < .68:
                continue
            cov = _project(region, size)
            current = grid == color
            if not .15 <= cov[current].sum() / cov.sum() <= .72:
                continue
            touches = _dilate(current & (cov >= .18), SIDES)
            return_color = (cov >= .55) & touches & (grid >= 0) & ~current
            if not return_color.any():
                continue
            grid[return_color] = color
            edits += int(return_color.sum())
    return edits


def _motif_options(mask, retained, size, axis, toward):
    coverage0 = _project(mask, size)
    shape = (mask.shape[1], mask.shape[0])
    image = mask.astype(np.float32)
    out = []
    for magnitude in (0, .25, .5):
        dx = toward * magnitude * SUBCELLS if axis == 1 else 0
        dy = toward * magnitude * SUBCELLS if axis == 0 else 0
        shifted = cv2.warpAffine(image, np.array([[1, 0, dx], [0, 1, dy]], dtype=float), shape)
        cells = (_project(shifted, size) >= .50) & retained
        if len(_components(cells, 4)[1]) == 2:
            error = float((coverage0 + cells-2 * coverage0 * cells).sum())
            out.append((error, cells))
    return out


def _preserve_two_tone_islands(opaque, labels, distance, grid):
    """Keep separate ink shapes on isolated, two-color motifs."""
    size = len(grid)
    islands, props = _components(opaque)
    retained = grid >= 0
    edited = 0
    if len(props) < 3:
        return edited
    for ident, (_, _, _, _, area) in enumerate(props[1:], 1):
        if not 14 * SUBCELLS**2 <= area <= .45 * opaque.sum():
            continue
        region = islands == ident
        counts = np.bincount(labels[region], minlength=distance.shape[0])
        primary = int(counts.argmax())
        alternate = np.where(distance[primary] >= 24, counts, 0)
        accent = int(alternate.argmax())
        if alternate[accent] < .10 * area or counts[accent] > .48 * area:
            continue
        mapped_labels = labels[region]
        near_primary = distance[primary, mapped_labels]
        near_accent = distance[accent, mapped_labels]
        if (np.minimum(near_primary, near_accent) <= 10).mean() < .94:
            continue
        binary = np.zeros_like(opaque)
        binary[region] = near_accent < near_primary
        pieces, stats = _components(binary)
        major = [i for i in range(1, len(stats)) if stats[i, cv2.CC_STAT_AREA] >= 2.5 * SUBCELLS**2]
        if len(major) != 2 or sum(stats[i, cv2.CC_STAT_AREA] for i in major) < .9 * binary.sum():
            continue
        masks = [pieces == i for i in major]
        centers = [np.mean(np.argwhere(m), axis=0) for m in masks]
        axis = int(np.argmax(np.abs(centers[0]-centers[1])))
        direction = -1 if centers[0][axis] < centers[1][axis] else 1
        cells = (_project(region, size) >= .20) & retained
        near_other = _project(opaque & ~region, size)
        cells &= near_other < .12
        alternatives = []
        for (err_a, a), (err_b, b) in product(_motif_options(masks[0], cells, size, axis, direction),
                                           _motif_options(masks[1], cells, size, axis, -direction)):
            if not (_dilate(a, SIDES) & b).any():
                alternatives.append((err_a + err_b, a, b))
        if not alternatives:
            continue
        _, a, b = min(alternatives, key=lambda option:option[0])
        originally = grid == accent
        # Refine only when the current accent merges the two components or
        # misses enough of one large component to affect its readability.
        existing, parts = _components(originally & cells, 4)
        ink_a = _project(masks[0], size)
        ink_b = _project(masks[1], size)
        merged = any(ink_a[existing == i].sum() >= 2 and ink_b[existing == i].sum() >= 2
                   for i in range(1, len(parts)))
        retained_fraction = min(ink_a[originally].sum() / ink_a.sum(), ink_b[originally].sum() / ink_b.sum())
        if not merged and retained_fraction >= .65:
            continue
        grid[cells] = primary
        grid[a | b] = accent
        edited += 1
    return edited


def refine_grid(rgba, ids, palette, color_limit):
    """Choose detail-preserving colors inside the existing board and palette."""
    size = ids.shape[0]
    detected = {"highlights": 0, "rings": 0, "islands": 0, "patches": 0, "flatness": 0.0}
    colors = sorted(set(ids.ravel()) - {None})
    if min(rgba.shape[:2]) <= size:
        return ids, detected
    detected['flatness'] = _flatness(rgba)
    if detected['flatness'] < .49:
        return ids, detected
    rgb, opaque = _resample(rgba, (size * SUBCELLS,) * 2)
    if not opaque.any():
        return ids, detected
    # A highlight can use one previously unused white bead if the color budget
    # has room. The extra code only appears if a source feature uses it.
    if (color_limit is None or len(colors) < color_limit) and all(
        min(palette[code]) <= 210 for code in colors
    ):
        whites = [
            code for code, color in palette.items()
            if min(color) > 220 and max(color) - min(color) < 25
        ]
        if whites:
            white_rgb = np.asarray([palette[code] for code in whites])
            goal = rgb2lab(np.full((1, 3), 252.0 / 255.0))
            match = int(np.argmin(deltaE_ciede2000(goal, rgb2lab(white_rgb / 255.0))))
            colors.append(whites[match])
    if len(colors) < 2:
        return ids, detected
    palette_rgb = np.asarray([palette[c] for c in colors], dtype=np.uint8)
    mapped, distance = _source_palette(rgb, opaque, palette_rgb)
    work = np.full(ids.shape, -1, dtype=np.int16)
    for i, code in enumerate(colors):
        work[ids == code] = i
    detected['patches'] = _recover_local_color(mapped, distance, palette_rgb, work)
    detected['highlights'], detected['rings'] = _preserve_enclosed_details(
        rgb, opaque, mapped, distance, palette_rgb, work
    )
    detected['islands'] = _preserve_two_tone_islands(opaque, mapped, distance, work)
    result = ids.copy()
    active = work >= 0
    result[active] = np.asarray(colors, dtype=object)[work[active]]
    return result, detected
