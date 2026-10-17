from jobhunt.config import RankingConfig
from jobhunt.jobs.models import Job
from jobhunt.ranking.rules import level_of, required_languages, triage


def job(**over):
    d = {
        "title": "Backend Engineer",
        "company": "Acme",
        "description": "Python.",
        "url": "u",
        "content_hash": "h",
        "location": "Berlin, Germany",
    }
    d.update(over)
    return Job(**d)


def test_senior_titles_and_years():
    assert level_of("Senior Backend Engineer", "") == "senior"
    assert level_of("Software Engineer III", "") == "senior"
    assert level_of("Backend Engineer", "You have 5+ years of experience in Go.") == "senior"
    assert level_of("Junior Developer", "") == "junior"
    assert level_of("Software Engineer I", "") == "junior"
    assert level_of("Backend Engineer", "2+ years of experience.") == "mid"


def test_years_need_experience_nearby():
    assert level_of("Backend Engineer", "We've been around for 5 years. Join us.") == "mid"


def test_required_languages():
    assert required_languages("Fluent German is required. English is a plus.") == ["german"]
    assert required_languages("Deutsch (C1) erforderlich.") == ["german"]
    assert required_languages("We work with German clients.") == []


def test_triage_filters_senior_and_language():
    cfg = RankingConfig()
    assert triage(job(title="Senior Engineer"), cfg).filtered_reason == "senior"
    assert (
        triage(job(description="Fluent Dutch required."), cfg).filtered_reason == "language:dutch"
    )
    assert triage(job(), cfg).filtered_reason is None


def test_triage_prefers_posted_pay_over_estimate():
    facts = triage(job(salary_text="EUR 60000-70000 yearly"), RankingConfig())
    assert (facts.pay_yearly_eur, facts.pay_estimated, facts.pay_source) == (65000, False, "posted")
    facts = triage(job(), RankingConfig())
    assert (facts.pay_yearly_eur, facts.pay_estimated) == (65_000, True)  # DE mid
    assert facts.country == "DE" and facts.visa == "easy"


def test_huge_digit_run_does_not_raise():
    assert level_of("Engineer", "9" * 5000 + " years of experience") == "mid"
