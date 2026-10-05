import unittest
from dataclasses import dataclass
from vstenv.filtering import Filter, choices


@dataclass
class Row:
    name: str
    vendor: str
    kind: str = "VST3"


ROWS = [
    Row("Kontakt 8", "Native Instruments"),
    Row("Massive X", "Native Instruments"),
    Row("SampleTank 4", "IK Multimedia"),
    Row("SWAM Violin", "Audio Modeling", kind="VST2"),
]
FIELDS = lambda r: (r.name, r.vendor, r.kind)
FACETS = {"vendor": lambda r: r.vendor, "kind": lambda r: r.kind}


class FilterTest(unittest.TestCase):
    def test_empty_filter_keeps_everything(self):
        f = Filter()
        self.assertFalse(f.active)
        self.assertEqual(len(f.apply(ROWS, FIELDS)), 4)

    def test_search_is_case_insensitive_substring(self):
        self.assertEqual([r.name for r in Filter("kontakt").apply(ROWS, FIELDS)], ["Kontakt 8"])

    def test_all_terms_must_match_in_any_order(self):
        """'ik tank' finds SampleTank: one term from the vendor, one from the name."""
        self.assertEqual([r.name for r in Filter("ik tank").apply(ROWS, FIELDS)], ["SampleTank 4"])
        self.assertEqual([r.name for r in Filter("tank ik").apply(ROWS, FIELDS)], ["SampleTank 4"])

    def test_a_term_matching_nothing_excludes_the_row(self):
        self.assertEqual(Filter("kontakt nonsense").apply(ROWS, FIELDS), [])

    def test_facet_is_exact_not_substring(self):
        f = Filter(facets={"vendor": "Native Instruments"})
        self.assertTrue(f.active)
        self.assertEqual(len(f.apply(ROWS, FIELDS, FACETS)), 2)
        self.assertEqual(Filter(facets={"vendor": "Native"}).apply(ROWS, FIELDS, FACETS), [])

    def test_facet_none_means_any(self):
        self.assertEqual(len(Filter(facets={"vendor": None}).apply(ROWS, FIELDS, FACETS)), 4)

    def test_facets_and_search_combine(self):
        f = Filter("massive", {"vendor": "Native Instruments"})
        self.assertEqual([r.name for r in f.apply(ROWS, FIELDS, FACETS)], ["Massive X"])
        self.assertEqual(Filter("massive", {"vendor": "IK Multimedia"}).apply(ROWS, FIELDS, FACETS), [])

    def test_unknown_facet_name_is_ignored_not_an_error(self):
        """A dropdown the caller did not describe must not empty the list."""
        self.assertEqual(len(Filter(facets={"nope": "x"}).apply(ROWS, FIELDS, FACETS)), 4)

    def test_whitespace_only_search_is_not_active(self):
        self.assertFalse(Filter("   ").active)
        self.assertEqual(len(Filter("   ").apply(ROWS, FIELDS)), 4)

    def test_none_fields_are_skipped(self):
        rows = [Row("A", None)]
        self.assertEqual(len(Filter("a").apply(rows, lambda r: (r.name, r.vendor))), 1)


class WildcardTest(unittest.TestCase):
    """Every term already matches anywhere, so a typed '*' must not break the
    search by being taken literally."""

    def test_stars_around_a_term_are_ignored(self):
        self.assertEqual([r.name for r in Filter("*tank*").apply(ROWS, FIELDS)], ["SampleTank 4"])

    def test_a_star_inside_a_term_is_ignored(self):
        self.assertEqual([r.name for r in Filter("sample*tank").apply(ROWS, FIELDS)], ["SampleTank 4"])

    def test_a_lone_star_means_everything(self):
        self.assertEqual(len(Filter("*").apply(ROWS, FIELDS)), 4)
        self.assertFalse(Filter("*").active)

    def test_question_mark_too(self):
        self.assertEqual([r.name for r in Filter("?tank").apply(ROWS, FIELDS)], ["SampleTank 4"])


class ChoicesTest(unittest.TestCase):
    def test_sorted_distinct_values(self):
        self.assertEqual(choices(ROWS, lambda r: r.vendor),
                         ["Audio Modeling", "IK Multimedia", "Native Instruments"])

    def test_case_insensitively_unique_keeping_the_first_spelling(self):
        rows = [Row("a", "FabFilter"), Row("b", "Fabfilter")]
        self.assertEqual(choices(rows, lambda r: r.vendor), ["FabFilter"])

    def test_blanks_are_dropped(self):
        rows = [Row("a", ""), Row("b", None), Row("c", "X")]
        self.assertEqual(choices(rows, lambda r: r.vendor), ["X"])


if __name__ == "__main__":
    unittest.main()
