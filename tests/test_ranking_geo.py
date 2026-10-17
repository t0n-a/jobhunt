from jobhunt.ranking.geo import country_of, mentions_sponsorship, visa_class


def test_country_from_city_country_and_state():
    assert country_of("Porto, Portugal") == "PT"
    assert country_of("Lisboa") == "PT"
    assert country_of("Berlin, Germany") == "DE"
    assert country_of("München") == "DE"
    assert country_of("London, England, United Kingdom") == "GB"
    assert country_of("San Francisco, CA") == "US"
    assert country_of("New York, NY, USA") == "US"
    assert country_of("Zürich") == "CH"
    assert country_of("Remote") is None
    assert country_of(None) is None
    assert country_of("Delhi, IN") == "IN"
    assert country_of("Springfield, IL") == "US"


def test_eu_is_easy_regardless_of_pay():
    assert visa_class("DE", "", None, 60_000) == "easy"
    assert visa_class("CH", "", None, 60_000) == "easy"
    assert visa_class(None, "", None, 60_000) == "easy"


def test_outside_eu_needs_sponsorship_or_pay():
    assert visa_class("US", "We offer visa sponsorship.", None, 60_000) == "ok"
    assert visa_class("US", "", 85_000, 60_000) == "ok"
    assert visa_class("GB", "", 40_000, 60_000) == "hard"


def test_negated_sponsorship_is_not_sponsorship():
    assert not mentions_sponsorship("We are unable to offer visa sponsorship for this role.")
    assert not mentions_sponsorship("No visa sponsorship available.")
    assert mentions_sponsorship("Relocation package and visa sponsorship provided.")


def test_negation_after_the_hit_negates_it():
    assert not mentions_sponsorship("Visa sponsorship is not available.")
    assert not mentions_sponsorship("Visa sponsorship: unavailable")
    assert not mentions_sponsorship("Visa sponsorship is not offered for this role.")
    assert mentions_sponsorship("Visa sponsorship is available. Not remote.")


def test_sponsor_phrasing_without_negation_is_detected():
    assert mentions_sponsorship("We are able to sponsor your visa.")
    assert mentions_sponsorship("We sponsor visas for this role.")


def test_us_alias_in_remote_locations_but_not_inside_words():
    for loc in ("Remote - US", "US Remote", "Remote (US)", "Austin, US"):
        assert country_of(loc) == "US"
    assert country_of("Brussels, Belgium") == "BE"
    assert country_of("Australia") == "AU"
    assert country_of("Russia") is None or country_of("Russia") != "US"


def test_negation_does_not_cross_clause_punctuation():
    assert mentions_sponsorship("We sponsor visas, no remote work")
    assert mentions_sponsorship("Visa sponsorship available, not for contractors")
    assert not mentions_sponsorship("We are unable to offer visa sponsorship.")

def test_us_state_code_beats_a_city_alias():
    assert country_of("Paris, TX") == "US"

def test_state_code_that_is_also_a_country_code_does_not_win():
    assert country_of("Delhi, IN") == "IN"

def test_san_francisco_ca_is_us():
    assert country_of("San Francisco, CA") == "US"
