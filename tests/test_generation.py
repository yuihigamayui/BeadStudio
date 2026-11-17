from pathlib import Path
import numpy as np
import pytest
from PIL import Image
from pydantic import ValidationError
from backend.generation import Strategy, geometry, generate
from backend.palette import load_palette
from backend.v3 import PIA

ROOT=Path(__file__).parents[1]

def test_v3_matches_selected_0202():
    if not (ROOT/'examples/test_images/0202.png').exists():
        pytest.skip('Private visual fixture is not distributed in GitHub source')
    with Image.open(ROOT/'examples/test_images/0202.png') as image:
        ids,rgb,ref,stats=generate(image,Strategy())
    with Image.open(ROOT/'examples/0202_V3_48.png') as expected:
        assert np.array_equal(rgb,np.asarray(expected))
    assert ids.shape==(48,48) and len(np.unique(ids))<=12
    assert set(ids.flat)<=set(load_palette().id)
    assert stats['iterations']==104 and stats['beads']==2304

@pytest.mark.parametrize('size',[10,100])
def test_sizes_alpha_and_budget(size):
    ids,rgb,_,stats=generate(Image.new('RGBA',(20,10),(255,0,0,0)),Strategy(size=size,colors=2))
    assert ids.shape==(size,size) and len(np.unique(ids))<=2
    # Current community card's near-white is H2=(254,255,255), not ideal white.
    assert rgb.min()>=250 and stats['isolated_regions']==0 and stats['regions']==1

def test_geometry_and_validation():
    image=Image.new('RGB',(100,50),'red')
    assert geometry(image,Strategy()).shape==(100,100,3)
    assert geometry(image,Strategy(fit='crop')).shape==(50,50,3)
    with pytest.raises(ValidationError): Strategy(algorithm='B0')
    with pytest.raises(ValidationError): Strategy(size=101)

def test_constraints_and_resume(tmp_path):
    y,x=np.indices((48,48)); rgb=np.stack((x*5,y*5,(x+y)*2),axis=2).astype(np.uint8)
    state=PIA(rgb,size=8,k=4).run()
    state.save(tmp_path/'state.npz'); resumed=PIA.load(tmp_path/'state.npz')
    before=resumed.iteration; color=resumed.colors[0].copy()
    resumed.set_pixel_constraint(2,3,[0]);resumed.set_palette_color(0,color,locked=True);resumed.run()
    assert resumed.assign[19]==0 and np.array_equal(resumed.colors[0],color)
    assert resumed.iteration>before and resumed.temperature==1
