import base64
import hashlib
import importlib.util
from pathlib import Path
import unittest

MODULE = Path(__file__).resolve().parents[2] / 'plugins/xrd/src/oaw_xrd/multiphase_structures.py'
spec = importlib.util.spec_from_file_location('multiphase_structures', MODULE)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)

CIF = '''data_phase
_cell_length_a 5
_cell_length_b 5
_cell_length_c 5
_cell_angle_alpha 90
_cell_angle_beta 90
_cell_angle_gamma 90
loop_
_atom_site_label
_atom_site_type_symbol
_atom_site_fract_x
_atom_site_fract_y
_atom_site_fract_z
Na1 Na 0.125 0.25 0.375
'''


@unittest.skipUnless(importlib.util.find_spec("pymatgen"), "Run in the OAW_XRDfit scientific environment")
class StructureTransportTests(unittest.TestCase):
    def test_each_phase_keeps_its_atoms_and_receives_its_own_fitted_cell(self):
        from pymatgen.io.cif import CifFile
        candidates = {'a': {'source_cif': {'text': CIF}}, 'b': {'source_cif': {'text': CIF.replace('Na1 Na', 'K1 K')}}}
        structures = module.fitted_structures(candidates, ['b', 'a'], [[6, 6, 6, 90, 90, 90], [5.1, 5.1, 5.1, 90, 90, 90]], source_kind='profile_cell_fit')
        for row, symbol, length in zip(structures, ['K', 'Na'], [6, 5.1]):
            raw = base64.b64decode(row['cif']['source_base64'])
            self.assertEqual(hashlib.sha256(raw).hexdigest(), row['cif']['sha256'])
            data = next(iter(CifFile.from_str(raw.decode()).data.values())).data
            self.assertEqual(data['_atom_site_type_symbol'], [symbol])
            self.assertEqual(data['_atom_site_fract_x'], ['0.125'])
            self.assertAlmostEqual(float(data['_cell_length_a']), length)

    def test_missing_or_invalid_cell_never_substitutes_another_phase(self):
        values = module.fitted_structures({'a': {'source_cif': {'text': CIF}}}, ['a'], [[5,5,5,1,1,179]], source_kind='pywpem_cell_fit')
        self.assertNotIn('cif', values[0])
        self.assertIn('positive volume', values[0]['error'])

    def test_frozen_input_hash_is_checked(self):
        values = module.fitted_structures({'a': {'source_cif': {'text': CIF, 'sha256': 'bad'}}}, ['a'], [[5,5,5,90,90,90]], source_kind='profile_cell_fit')
        self.assertNotIn('cif', values[0])
        self.assertIn('SHA256', values[0]['error'])


if __name__ == '__main__':
    unittest.main()
