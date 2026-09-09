from jobhunt.linkedin.diff import diff_field


def test_identical_text_is_all_the_same():
    assert diff_field("Built a shortener", "Built a shortener") == [("same", "Built a shortener")]


def test_an_addition_is_marked_added():
    segments = diff_field("Built a shortener", "Built a fast shortener")
    assert ("added", "fast") in segments
    assert [text for kind, text in segments if kind == "removed"] == []


def test_a_removal_is_marked_removed():
    segments = diff_field("Built a fast shortener", "Built a shortener")
    assert ("removed", "fast") in segments


def test_a_replacement_reports_both_sides():
    segments = diff_field("Built in Flask", "Built in Django")
    assert ("removed", "Flask") in segments
    assert ("added", "Django") in segments


def test_the_first_version_has_nothing_to_diff_against():
    assert diff_field("", "Built a shortener") == [("same", "Built a shortener")]


def test_two_empty_strings_produce_no_segments():
    assert diff_field("", "") == []


def test_the_segments_reassemble_the_current_text():
    # The template renders these in order, so anything that is in `current`
    # and in no "same"/"added" segment would silently vanish from the page.
    previous = "Built a link shortener in Flask, cached in Redis"
    current = "Built a URL shortener in Flask, backed by Redis"
    segments = diff_field(previous, current)
    rebuilt = " ".join(text for kind, text in segments if kind in {"same", "added"})
    assert rebuilt == current
