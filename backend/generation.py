"""Production V3: author-source PIA port -> independent CIEDE2000 card map."""
import time
from typing import Literal, get_args
import cv2
import numpy as np
from PIL import Image, ImageOps
from pydantic import BaseModel, Field, model_validator
from skimage.color import rgb2lab, deltaE_ciede2000
from backend.v3 import PIA
from backend.palette import load_palette
from backend.flat_pattern import generate_flat
from backend.pindou_pattern import generate_pindou

Algorithm = Literal['V3', 'flat', 'pindou', 'pindou_refine']
MARD_ALGORITHMS = get_args(Algorithm)


class Strategy(BaseModel):
    algorithm: Algorithm = 'V3'
    size: int = Field(48, ge=10, le=100)
    colors: int | None = Field(12, ge=2, le=32)
    fit: Literal['crop', 'pad'] = 'pad'
    subject_scale: float = Field(1, ge=.5, le=2)

    @model_validator(mode='after')
    def require_v3_color_limit(self):
        if self.algorithm == 'V3' and self.colors is None:
            raise ValueError('V3 需要指定颜色上限')
        return self


def _square_frame(image, strategy, background):
    w, h = image.size
    side = min(w, h) if strategy.fit == 'crop' else max(w, h)
    side = max(1, round(side/strategy.subject_scale))
    left, top = (w-side)//2, (h-side)//2
    canvas = Image.new(image.mode, (side, side), background)
    box = (max(0,left), max(0,top), min(w,left+side), min(h,top+side))
    canvas.paste(image.crop(box), (box[0]-left,box[1]-top))
    if side < strategy.size:
        canvas = canvas.resize((strategy.size,strategy.size),Image.Resampling.NEAREST)
    return canvas


def geometry(image, strategy):
    image = ImageOps.exif_transpose(image).convert('RGBA')
    white = Image.new('RGBA', image.size, 'white')
    white.alpha_composite(image)
    return np.asarray(_square_frame(white.convert('RGB'), strategy, 'white'))


def generate(image, strategy):
    if strategy.algorithm in {'flat', 'pindou', 'pindou_refine'}:
        rgba = ImageOps.exif_transpose(image).convert('RGBA')
        rgba.thumbnail((1600, 1600), Image.Resampling.LANCZOS)
        frame = _square_frame(rgba, strategy, (0, 0, 0, 0))
        if strategy.algorithm == 'flat':
            return generate_flat(frame, strategy.size, strategy.colors)
        return generate_pindou(
            frame, strategy.size, strategy.colors, refine=strategy.algorithm == 'pindou_refine'
        )

    start = time.perf_counter()
    source = geometry(image, strategy)
    state = PIA(source, size=strategy.size, k=strategy.colors, profile='author').run()
    ids, rgb = state.bead(load_palette())
    reference = cv2.resize(source,(strategy.size,strategy.size),interpolation=cv2.INTER_AREA)
    # AREA is only the quality reference, not the V3 generator.
    error = deltaE_ciede2000(rgb2lab(reference/255.),rgb2lab(rgb/255.)).mean()
    seen = np.zeros(ids.shape,bool); sizes=[]
    h,w = ids.shape
    for y in range(h):
        for x in range(w):
            if seen[y,x]: continue
            seen[y,x]=True; stack=[(y,x)]; count=0
            while stack:
                cy,cx=stack.pop(); count+=1
                for ny,nx in ((cy-1,cx),(cy+1,cx),(cy,cx-1),(cy,cx+1)):
                    if 0<=ny<h and 0<=nx<w and not seen[ny,nx] and ids[ny,nx]==ids[cy,cx]:
                        seen[ny,nx]=True; stack.append((ny,nx))
            sizes.append(count)
    return ids,rgb,reference,dict(mean_delta_e2000=float(error),isolated_regions=sizes.count(1),
        small_regions=sum(n<=3 for n in sizes),regions=len(sizes),colors=len(np.unique(ids)),
        beads=ids.size,iterations=state.iteration,processing_ms=(time.perf_counter()-start)*1000)
