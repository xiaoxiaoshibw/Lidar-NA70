import unittest
import numpy as np
from run import masks


class RetentionContract(unittest.TestCase):
    def test_touching_body_stays_unknown_and_above_band_is_unchanged(self):
        cfg={'z_min_m':-.25,'z_max_m':2.6,'low_band_upper_m':.1}
        p=np.array([[1,0,.01],[1,0,.09],[1,0,.4],[1,0,.5],[1,0,-.3],[np.nan,0,.01]])
        domain,frozen,rescued,protected=masks(p,np.array([.01,.03,.01,.3,.01,.01]),.055,cfg)
        self.assertEqual(frozen.tolist(),[False,False,False,True,False,False])
        self.assertEqual(rescued.tolist(),[True,True,False,False,False,False])
        self.assertEqual(protected.tolist(),[True,True,False,True,False,False])
        self.assertTrue(np.all(protected <= domain))
        self.assertFalse(np.any(frozen & rescued))


if __name__=='__main__': unittest.main()
