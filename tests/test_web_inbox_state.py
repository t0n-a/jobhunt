from datetime import UTC, datetime, timedelta

from jobhunt.jobs.models import Job, Score
from jobhunt.tailor.models import Application
from jobhunt.web.inbox import ago, chart_points, fit_y, job_state

JOB = Job(id=7, title="T", company="C", description="d", url="u", content_hash="h")


def score(fit=80):
    return Score(job_id=7, fit_score=fit, track="backend", seniority_fit="match", rationale="r", content_hash="h")


def app(status="drafted", form_plan=None, bullets=()):
    resume = {"sections": [{"entries": [{"bullets": [{"text": "x", "source": s} for s in bullets]}]}]}
    return Application(id=3, job_id=7, status=status, form_plan=form_plan, resume_json=resume)


def field(required):
    return {"name": "q", "label": "Q", "kind": "text", "required": required, "options": []}


def test_no_score_no_application_is_unscored():
    assert job_state(JOB, None, None).key == "unscored"


def test_scored_without_application_is_ranked_and_points_at_the_job():
    st = job_state(JOB, score(), None)
    assert (st.key, st.href) == ("ranked", "/jobs/7")


def test_queued_application_waits_for_the_users_yes():
    st = job_state(JOB, score(), app("queued"))
    assert (st.key, st.label, st.href) == ("waiting", "needs your yes", "/applications/3")


def test_drafted_application_is_the_users_to_finish():
    assert job_state(JOB, score(), app()).key == "drafted"


def test_required_unanswered_form_question_blocks():
    plan = {"filled": [], "unknown": [field(True), field(True), field(False)], "dropped": []}
    st = job_state(JOB, score(), app(form_plan=plan))
    assert (st.key, st.next) == ("blocked", "Answer 2 form questions")


def test_only_optional_unknowns_do_not_block():
    plan = {"filled": [], "unknown": [field(False)], "dropped": []}
    assert job_state(JOB, score(), app("queued", form_plan=plan)).key == "waiting"


def test_queued_with_a_required_unknown_is_blocked_too():
    plan = {"filled": [], "unknown": [field(True)], "dropped": []}
    assert job_state(JOB, score(), app("queued", form_plan=plan)).key == "blocked"


def test_user_written_bullets_count_as_unverified():
    assert job_state(JOB, score(), app(bullets=("user", "atom", "user"))).unverified == 2


def test_later_statuses():
    assert job_state(JOB, score(), app("approved")).key == "approved"
    assert job_state(JOB, score(), app("interview")).label == "interview"
    assert job_state(JOB, score(), app("interview")).key == "sent"
    assert job_state(JOB, score(), app("ghosted")).key == "closed"


def test_ago():
    now = datetime(2026, 10, 17, 12, tzinfo=UTC)
    assert ago(now - timedelta(minutes=5), now) == "5m ago"
    assert ago(now - timedelta(hours=3), now) == "3h ago"
    assert ago((now - timedelta(days=2)).replace(tzinfo=None), now) == "2d ago"


def test_chart_skips_unscored_and_pins_old_jobs_left():
    now = datetime(2026, 10, 17, tzinfo=UTC)
    old = Job(id=1, title="a", company="c", description="d", url="u", content_hash="h", first_seen=now - timedelta(days=60))
    new = Job(id=2, title="b", company="c", description="d", url="u", content_hash="h", first_seen=now)
    bare = Job(id=3, title="c", company="c", description="d", url="u", content_hash="h", first_seen=now)
    scores = {1: score(95), 2: score(30)}
    states = {i: job_state(j, scores.get(i), None) for i, j in ((1, old), (2, new), (3, bare))}
    pts = {p.job_id: p for p in chart_points([old, new, bare], scores, states, now)}
    assert set(pts) == {1, 2}
    assert (pts[1].x, pts[2].x) == (50.0, 500.0)
    assert pts[2].y == fit_y(40)  # below-axis fits sit on the axis
