"""V3 card and compatibility with saved V2 demo IDs."""
from functools import lru_cache
from pathlib import Path
import json
import pandas as pd
ROOT = Path(__file__).parent/'palettes'

@lru_cache(maxsize=2)
def load_palette(legacy=False):
    if legacy:
        demo = pd.read_csv(ROOT/'demo_palette.csv', dtype={'id': str})
        demo['legacy'] = True
        return demo
    current = pd.DataFrame(json.loads((ROOT/'mard_221_community.json').read_text(encoding='utf-8')))
    if current.id.duplicated().any():
        raise ValueError('Duplicate palette IDs')
    current = current.drop_duplicates(['r', 'g', 'b']).reset_index(drop=True)
    current['legacy'] = False
    return current
