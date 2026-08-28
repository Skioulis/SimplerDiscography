"""Accent folding — the basis of accent-insensitive search."""

from models import fold


def test_strips_greek_tonos():
    assert fold("Νοσταλγία") == "νοσταλγια"


def test_case_insensitive():
    assert fold("ΝΟΣΤΑΛΓΙΑ") == fold("νοσταλγία")


def test_strips_latin_accents():
    assert fold("Café") == "cafe"


def test_micro_sign_folds_to_greek_mu():
    """The archives mix U+00B5 MICRO SIGN with U+03BC GREEK SMALL LETTER MU.

    Without this, "Ρεµπέτικο" (2,619 rows in the 78s) and "Ρεμπέτικο" (1,770)
    are unrelated strings and a search finds only one of them.
    """
    micro, mu = "Ρεµπέτικο", "Ρεμπέτικο"
    assert micro != mu                      # genuinely different on disk
    assert fold(micro) == fold(mu) == "ρεμπετικο"


def test_empty_and_none():
    assert fold(None) == ""
    assert fold("") == ""
