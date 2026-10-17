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
    assert not mentions_sponsorship("We are unable to sponsor visas for this role.")
    assert not mentions_sponsorship("No visa sponsorship available.")
    assert mentions_sponsorship("Relocation package and visa sponsorship provided.")
