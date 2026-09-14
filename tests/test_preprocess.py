import unittest
from app.preprocess import hk_mo_price, normalize_currency_markers, normalize_price, preprocess_vip_and_star_prices, recommended_price

class SasaPricePreprocessingTests(unittest.TestCase):

    def test_region_uses_legacy_mode_or_an_explicit_canonical_value(self) -> None:
        self.assertIsNone(hk_mo_price({}))
        self.assertIsNone(hk_mo_price({'hk_mo_price': ''}))
        self.assertIsNone(hk_mo_price({'hk_mo_price': '   '}))
        self.assertEqual('HK', hk_mo_price({'hk_mo_price': 'HK'}))
        self.assertEqual('MO', hk_mo_price({'hk_mo_price': 'MO'}))
        for invalid in ('hk', 'mo', 'MOP', 'US'):
            with self.subTest(invalid=invalid):
                with self.assertRaisesRegex(ValueError, "must be 'HK' or 'MO'"):
                    hk_mo_price({'hk_mo_price': invalid})
        with self.assertRaisesRegex(TypeError, 'must be a string'):
            hk_mo_price({'hk_mo_price': ['HK']})

    def test_legacy_price_mode_preserves_markers_and_inserts_dollars(self) -> None:
        self.assertEqual('MOP180', normalize_price('MOP180'))
        self.assertEqual('$180', normalize_price('$180'))
        self.assertEqual('$180/件', normalize_price('180/件'))
        self.assertEqual('MOP200', recommended_price('建議價 MOP200'))

    def test_auxiliary_text_replaces_markers_without_inserting_them(self) -> None:
        value = 'Gift 30ml: $99; VIP MOP100; 67折'
        self.assertEqual(value, normalize_currency_markers(value, None))
        self.assertEqual('Gift 30ml: $99; VIP $100; 67折', normalize_currency_markers(value, 'HK'))
        self.assertEqual('Gift 30ml: MOP99; VIP MOP100; 67折', normalize_currency_markers(value, 'MO'))
        self.assertIsNone(normalize_currency_markers(None, 'MO'))

    def test_normalize_price_replaces_or_inserts_target_marker(self) -> None:
        cases = (('$180', 'HK', '$180'), ('MOP180', 'HK', '$180'), ('180/件', 'HK', '$180/件'), ('$180', 'MO', 'MOP180'), ('MOP180', 'MO', 'MOP180'), ('180/件', 'MO', 'MOP180/件'), ('248', 'MO', 'MOP248'), ('$248', 'MO', 'MOP248'), ('MOP248', 'HK', '$248'), ('MOPMOP1,234', 'HK', '$1,234'), ('$$1,234', 'MO', 'MOP1,234'))
        for value, region, expected in cases:
            with self.subTest(value=value, region=region):
                self.assertEqual(expected, normalize_price(value, region))
        self.assertEqual('MOP189/2件\n(每件平均MOP94.5)', normalize_price('$189/2件\n(每件平均$94.5)', 'MO'))
        self.assertEqual('第2件免費', normalize_price('第2件免費', 'MO'))
        self.assertEqual('$200', recommended_price('建議價 MOP200', 'HK'))
        self.assertEqual('MOP200', recommended_price('建議價 $200', 'MO'))

    def test_vip_and_star_price_selection(self) -> None:
        cases = (('$180', '$278/2件', ('$180', '$278/2件')), ('$180', '.', ('$180', None)), ('$180', '優惠2件價', ('$180', '優惠2件價')), (None, '.', ('', '.')), ('$180', None, ('$180', None)), ('$180', '', ('$180', None)), ('$180', '   ', ('$180', None)), (None, None, ('', None)))
        for vip_price, star_price, expected in cases:
            with self.subTest(vip_price=vip_price, star_price=star_price):
                self.assertEqual(expected, preprocess_vip_and_star_prices(vip_price, star_price))
