import sys, unittest, os
from pathlib import Path
import numpy as np
from pymatgen.core import Lattice
sys.path.insert(0, str(Path(os.environ['OAW_XRD_ROOT'])/'PyWPEM'))
from src.EMBraggOpt.bounded_cell import angles, optimize_cell, stable_peak_center
from src.EMBraggOpt.BraggLawDerivation import BraggLawDerivation
class CellTests(unittest.TestCase):
 def test_spacing_matches_independent_metric_for_all_systems(self):
  for cell in [[5,5,5,90,90,90],[5,5,7,90,90,120],[5,5,7,90,90,90],[5,6,7,90,90,90],[5,5,5,78,78,78],[5,6,7,90,104,90],[7.349,9.252,12.523,108.01,93.48,94.4]]:
   hkl=np.array([[1,0,0],[1,1,0],[1,1,1],[2,1,-1]])
   expected=[2*np.rad2deg(np.arcsin(1.540593/(2*Lattice.from_parameters(*cell).d_hkl(v)))) for v in hkl]
   np.testing.assert_allclose(angles(cell,hkl,[1.540593]),expected,rtol=1e-10)
 def test_triclinic_symbolic_formula(self):
  cell=[7.349,9.252,12.523,108.01,93.48,94.4]
  d,_,_=BraggLawDerivation().get_d_space(7,[1,2],[1,1],[1,-1],*cell)
  np.testing.assert_allclose(d,[Lattice.from_parameters(*cell).d_hkl(v) for v in [[1,1,1],[2,1,-1]]],rtol=1e-10)
 def test_recovers_small_triclinic_change(self):
  cell=np.array([7.349,9.252,12.523,108.01,93.48,94.4]); new=cell.copy(); new[:3]*=1.002;new[3:]+=.08
  hkl=np.array([[1,0,0],[0,1,0],[0,0,1],[1,1,0],[1,0,1],[0,1,1],[2,1,1],[1,2,1],[1,1,2]])
  old=angles(cell,hkl,[1.540593]);target=angles(new,hkl,[1.540593])
  out=optimize_cell(7,old,target,11,0,100,*hkl.T,cell,[1.540593])
  np.testing.assert_allclose(out[:6],new,atol=1e-5)
 def test_extreme_targets_remain_bounded_and_real(self):
  hkl=np.array([[i,j,k] for i in range(1,4) for j in range(1,3) for k in range(1,3)])
  cell=np.array([7.349,9.252,12.523,108.01,93.48,94.4]); old=angles(cell,hkl,[1.540593])
  out=optimize_cell(7,old,np.full(len(old),65),11,0,100,*hkl.T,cell,[1.540593]);updated=np.array(out[:6])
  self.assertTrue(np.all(np.abs(updated[:3]/cell[:3]-1)<=.01000001));self.assertTrue(np.all(np.abs(updated[3:]-cell[3:])<=.500001))
  self.assertTrue(np.all(np.isfinite(angles(updated,hkl,[1.540593]))))
 def test_unobserved_peak_retains_center(self):
  self.assertEqual(stable_peak_center([0,0],[0,0],27.3),27.3)
  self.assertEqual(stable_peak_center([20,30],[2,3],27.3),20)
  with self.assertRaises(ValueError):stable_peak_center([np.nan,0],[1,1],27.3)
 def test_invalid_metric_rejected(self):
  with self.assertRaises(ValueError):angles([5,5,5,1,1,179],np.array([[1,1,1]]),[1.54])
if __name__=='__main__':unittest.main()
