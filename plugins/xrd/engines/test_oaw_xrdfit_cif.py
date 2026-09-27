"""Regression checks for noninteractive CIF preprocessing."""
import unittest
from types import SimpleNamespace
from unittest.mock import patch
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src/oaw_xrd'))
from OAW_XRDfit.src.Extinction import XRDpre

class CifSpaceGroupTests(unittest.TestCase):
    def read(self, tags):
        data = {'_cell_length_a': '8.887(17)', '_cell_length_b': '10.048(19)',
                '_cell_length_c': '12.94(2)', '_cell_angle_alpha': '104.87(2)',
                '_cell_angle_beta': '101.59(3)', '_cell_angle_gamma': '111.32(3)',
                '_space_group_IT_number': '2', '_atom_site_type_symbol': ['C'],
                '_atom_site_fract_x': ['0'], '_atom_site_fract_y': ['0'],
                '_atom_site_fract_z': ['0'],
                '_space_group_symop_operation_xyz': ['x,y,z', '-x,-y,-z'], **tags}
        cif = SimpleNamespace(data={'test': SimpleNamespace(data=data)})
        with patch.object(XRDpre.CifFile, 'from_file', return_value=cif):
            return XRDpre.read_cif('test.cif')

    def test_modern_and_legacy_names(self):
        for tag in ('_space_group_name_H-M_alt', '_symmetry_space_group_name_H-M'):
            with self.subTest(tag=tag):
                value = self.read({tag: 'P -1'})
                self.assertEqual(value[1], 'P -1')
                self.assertEqual(value[3], 'P')
                self.assertEqual(value[2][0], 2)
                self.assertEqual(len(value[4]), 2)

    def test_hall_inversion_prefix(self):
        for tag in ('_space_group_name_Hall', '_symmetry_space_group_name_Hall'):
            self.assertEqual(self.read({tag: '-P 1'})[3], 'P')

    def test_unknown_name_falls_back_to_hall(self):
        self.assertEqual(self.read({'_space_group_name_H-M_alt': '?',
                                   '_space_group_name_Hall': '-P 1'})[3], 'P')

    def test_missing_information_never_prompts(self):
        value = self.read({})
        with patch.object(XRDpre, 'read_cif', return_value=value), patch(
                'builtins.input', side_effect=AssertionError('interactive input forbidden')):
            with self.assertRaisesRegex(ValueError, 'space-group centering'):
                XRDpre.profile.__new__(XRDpre.profile).generate('test.cif')

if __name__ == '__main__':
    unittest.main()
