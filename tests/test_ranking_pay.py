from jobhunt.ranking.pay import Salary, annual_eur, estimate_pay, parse_salary


def test_parses_eur_range_per_year():
    assert parse_salary("€45,000 - €55,000 per year") == Salary(45000, 55000, "EUR", "year")

def test_parses_eu_grouping_and_trailing_symbol():
    assert parse_salary("45.000€ - 55.000€ brutos/ano") == Salary(45000, 55000, "EUR", "year")

def test_parses_k_suffix_and_dollar():
    assert parse_salary("$120k–$150k") == Salary(120000, 150000, "USD", "year")

def test_decimal_k():
    assert parse_salary("45,5k EUR") == Salary(45500, 45500, "EUR", "year")

def test_monthly_gbp_to_annual_eur():
    s = parse_salary("£3,500 per month")
    assert s == Salary(3500, 3500, "GBP", "month")
    assert annual_eur(s) == 49140

def test_huge_grouped_number_does_not_raise():
    s = parse_salary("EUR 1" + ",000" * 1500 + " yearly")
    assert s is None or isinstance(s, Salary)

def test_hourly():
    assert annual_eur(parse_salary("EUR 40/hour")) == 83200

def test_years_of_experience_is_not_a_salary():
    assert parse_salary("5+ years of experience with Python") is None
    assert parse_salary(None) is None

def test_absurd_amount_is_rejected():
    assert annual_eur(Salary(5, 5, "EUR", "year")) is None

def test_estimate_by_country_and_level():
    assert estimate_pay("DE", "junior") == 50_000
    assert estimate_pay("DE", "mid") == 65_000
    assert estimate_pay(None, "junior") is None
    assert estimate_pay("ZZ", "junior") is None


def test_million_funding_is_not_a_salary():
    assert parse_salary("Backed by $10M funding, 50 hours a week") is None
    assert parse_salary("Raised EUR 2 million per year in growth") is None


def test_skips_a_bonus_to_find_the_real_salary():
    s = parse_salary("$5,000 signing bonus. Salary 80000 EUR per year")
    assert s == Salary(80000, 80000, "EUR", "year")


def test_period_word_far_from_the_number_does_not_count():
    assert parse_salary("We pay EUR 3000 sometimes." + " " * 40 + "Offices open every day") is None
