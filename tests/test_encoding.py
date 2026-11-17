from pathlib import Path
import re

def test_chinese_labels():
    root=Path(__file__).parents[1] / 'frontend/src'
    source = '\n'.join(p.read_text(encoding='utf-8') for p in root.glob('*.tsx'))
    for label in ['画笔', '取色', '橡皮擦', '连通填充', '保存为新版本', '材料统计']:
        assert label in source
    assert not re.search(r'\?{2,}', source)
