"""Scientific regressions run in the configured NumPy/SciPy interpreter."""
import copy
import math
from pathlib import Path
import sys
import unittest

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'plugins/xrd/src/oaw_xrd'))
from matching import align_reference_peaks, match_patterns

WAVELENGTH = 1.540593


def spacings(angles):
    return WAVELENGTH / (2 * np.sin(np.radians(np.asarray(angles) / 2)))


def angles(ds):
    return np.degrees(2 * np.arcsin(WAVELENGTH / (2 * np.asarray(ds))))


class CellScaleTests(unittest.TestCase):
    def align(self, reference, observed, window=.02, tolerance=.03, limits=(10., 140.)):
        return align_reference_peaks(np.asarray(observed), np.ones(len(observed)), np.asarray(reference),
            np.ones(len(reference)), WAVELENGTH, tolerance, window, limits)

    def test_exact_bragg_scaling_across_low_and_high_angles(self):
        ds = spacings([12, 20, 32, 46, 64, 89, 125])
        fitted, raw = self.align(ds, angles(ds * .993))
        self.assertAlmostEqual(fitted['scale'], .993, places=7)
        self.assertEqual(len(fitted['rows']), 7)
        self.assertLess(fitted['mean_delta'], 1e-6)
        self.assertGreater(fitted['score'], raw['score'])

    def test_zero_window_keeps_the_original_strict_match(self):
        ds = spacings([15, 25, 40, 60, 90])
        fitted, raw = self.align(ds, angles(ds * .993), window=0)
        self.assertEqual(fitted['scale'], 1)
        self.assertEqual(fitted['score'], raw['score'])
        self.assertEqual(len(fitted['rows']), 0)

    def test_scale_is_bounded_and_two_peaks_cannot_justify_it(self):
        ds = spacings([13, 24, 38, 57, 82, 109])
        fitted, _ = self.align(ds, angles(ds * .96), tolerance=.02)
        self.assertTrue(.98 <= fitted['scale'] <= 1.02)
        self.assertLess(len(fitted['rows']), 3)
        fitted, _ = self.align(ds[:2], angles(ds[:2] * .993))
        self.assertEqual(fitted['scale'], 1)

    def test_independent_peak_offsets_do_not_become_a_global_scale(self):
        ds = spacings([13, 24, 38, 57, 82, 109])
        fitted, _ = self.align(ds, angles(ds * np.array([.991, 1.012, .986, 1.018, .994, 1.006])), tolerance=.005)
        self.assertEqual(fitted['scale'], 1)
        self.assertLess(len(fitted['rows']), 3)

    def test_one_observation_cannot_support_multiple_reference_peaks(self):
        ds = spacings([30, 30.01, 30.02, 60])
        fitted, _ = self.align(ds, [30.1, 60.2, 90], tolerance=.15)
        self.assertEqual(len(fitted['rows']), len(set(fitted['rows'])))
        self.assertEqual(fitted['scale'], 1)

    def test_peaks_crossing_the_measured_boundary_are_retained(self):
        ds = spacings([9.96, 20, 40, 60])
        fitted, raw = self.align(ds, angles(ds * .99), limits=(10, 65))
        self.assertEqual(len(raw['indexes']), 3)
        self.assertEqual(len(fitted['rows']), 4)
        self.assertAlmostEqual(fitted['scale'], .99, places=7)

    def test_unchanged_reference_prefers_unit_scale_and_invalid_windows_fail(self):
        ds = spacings([15, 25, 40, 60])
        fitted, _ = self.align(ds, angles(ds))
        self.assertEqual(fitted['scale'], 1)
        for window in [-.01, .06, float('nan')]:
            with self.assertRaises(ValueError):
                self.align(ds, angles(ds), window=window)

    def test_zero_intensity_peaks_cannot_supply_scale_evidence(self):
        ds = spacings([15, 25, 40, 60])
        fitted, _ = align_reference_peaks(angles(ds*.993), np.ones(4), ds,
            np.array([100.,0.,0.,0.]), WAVELENGTH, .03, .02, (10,140))
        self.assertEqual(fitted['scale'], 1.)
        self.assertTrue(math.isfinite(fitted['score']))

    def test_end_to_end_keeps_source_data_and_reports_raw_and_aligned_positions(self):
        ds = spacings([15, 24, 37, 53, 72])
        observed = angles(ds * .993)
        grid = np.arange(10, 85, .01)
        signal = np.full(len(grid), 100.)
        for angle in observed:
            signal += 1000 * np.exp(-((grid - angle) / .04) ** 2)
        inputs = [{'node_id':'p','revision':1,'value':{'kind':'pattern','filename':'synthetic',
            'sha256':'pattern','metadata':{},'points':np.column_stack([grid,signal]).tolist()}},
            {'node_id':'r','revision':1,'value':{'kind':'reference','filename':'reference','sha256':'reference',
                'metadata':{},'peaks':[{'d':float(d),'intensity':100.,'hkl':[1,0,0]} for d in ds]}}]
        original = copy.deepcopy(inputs)
        options = {'wavelength':WAVELENGTH,'tolerance_deg':.05,'cell_scale_percent':2.,
            'prominence_fraction':.03,'smoothing_deg':.02,'min_peak_distance':.1,'reference_min_intensity':5}
        result = match_patterns(inputs,options)
        candidate = result['candidates'][0]
        self.assertEqual(inputs, original)
        self.assertAlmostEqual(candidate['alignment']['cell_scale'], .993, delta=.0002)
        self.assertEqual(candidate['matched_reference_count'], 5)
        self.assertGreater(max(abs(m['raw_delta']) for m in candidate['matches']), .4)
        self.assertLess(max(abs(m['delta']) for m in candidate['matches']), .02)
        for peak in candidate['peaks']:
            self.assertAlmostEqual(peak['d'],peak['original_d']*candidate['alignment']['cell_scale'])
        self.assertEqual(result['parameters']['cell_scale_percent'],2.)


if __name__ == '__main__':
    unittest.main(verbosity=2)
