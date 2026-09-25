import importlib.util
from pathlib import Path
import tempfile
import unittest
try:
 import numpy as np
 import pandas as pd
except ImportError:
 np = None
spec=importlib.util.spec_from_file_location('components',Path(__file__).parents[2]/'plugins/xrd/src/oaw_xrd/multiphase_components.py')
module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
@unittest.skipIf(np is None, "run in scientific Python for numpy/pandas")
class ComponentTests(unittest.TestCase):
 def test_zero_area_undefined_mixing_ratio_is_zero_contribution(self):
  with tempfile.TemporaryDirectory() as d:
   r=Path(d)/'DecomposedComponents';r.mkdir()
   pd.DataFrame([[0,np.nan,20,.1,.01],[2,.5,20,.1,.01]],columns=['wi','Ai','mu_i','L_gamma_i','G_sigma2_i']).to_csv(r/'sub_peaks.csv',index=False)
   x=np.array([19.9,20.,20.1]);dx=x-20
   y=2*(.5*.1/(np.pi*(dx**2+.01))+.5*np.exp(-dx**2/.02)/np.sqrt(2*np.pi*.01))
   points=np.column_stack((x,y));np.savetxt(r/'upbackground.csv',np.column_stack((x,np.zeros(3))),delimiter=',')
   curves,audit=module.phase_components(d,['a'],['A'],points,y,np.arange(3))
   self.assertLess(audit['maximum_relative_closure_error'],1e-12)
   self.assertEqual(len(curves),1)
   table=pd.read_csv(r/'sub_peaks.csv');table.loc[0,'wi']=1;table.to_csv(r/'sub_peaks.csv',index=False)
   with self.assertRaisesRegex(ValueError,'Invalid'):module.phase_components(d,['a'],['A'],points,y,np.arange(3))
if __name__=='__main__':unittest.main()
