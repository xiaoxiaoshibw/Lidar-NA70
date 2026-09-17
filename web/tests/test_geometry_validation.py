"""SYNTHETIC fixed-structure geometry; not real acceptance."""
import numpy as np
import pytest
from backend.geometry_validation import measure

def contract():
    return dict(minimum=[-1,-1,-1],maximum=[1,1,1],origin=[0,0,0],normal=[1,0,0],
                histogram_bin_m=.01,max_thickness_m=.05,min_layer_separation_m=.1,min_peak_fraction=.1,min_points=20)

def test_wall_and_double_wall_candidates():
    rng=np.random.default_rng(42)
    points=rng.uniform(-.5,.5,(1000,3));points[:,0]=rng.normal(0,.001,1000)
    wall=measure(points,contract())
    assert wall['thickness_within_frozen_limit']
    assert not wall['double_layer_candidate']
    ghost=points.copy();ghost[:,0]+=.3
    doubled=measure(np.vstack([points,ghost]),contract())
    assert doubled['double_layer_candidate']
    assert not doubled['thickness_within_frozen_limit']

def test_sparse_roi_and_invalid_contract():
    assert measure(np.zeros((3,3)),contract())['status']=='NOT_EVALUATED'
    with pytest.raises(ValueError):measure(np.zeros((30,3)),contract()|{'normal':[0,0,0]})
