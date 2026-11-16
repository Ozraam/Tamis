import json

import pytest

from tamis import updates


@pytest.mark.parametrize(
    ("candidate", "current", "newer"),
    [
        ("0.2.0", "0.1.0", True),
        ("v0.10.0", "0.9.0", True),
        ("0.9.0", "0.10.0", False),
        ("0.1.0", "0.1.0", False),
        ("0.1", "0.1.0", False),
        ("0.1.1", "0.1", True),
        ("1.0.0", "unknown", False),
        ("0.3.0b1", "0.1.0", False),
        ("0.3.0-rc1", "0.1.0", False),
        ("nonsense", "0.1.0", False),
        (None, "0.1.0", False),
    ],
)
def test_versions_compare_numerically(candidate, current, newer):
    assert updates.is_newer(candidate, current) is newer


def answer(**fields):
    data = {"tag_name": "v0.2.0", "html_url": "https://github.com/Ozraam/Tamis/releases/tag/v0.2.0",
            "draft": False, "prerelease": False}
    data.update(fields)
    return json.dumps(data).encode()


def test_a_newer_release_is_found():
    result = updates.result_of(answer(), "0.1.0")
    assert result.newer == updates.Release("0.2.0", "https://github.com/Ozraam/Tamis/releases/tag/v0.2.0")
    assert result.latest == "0.2.0" and result.error is None


def test_the_same_version_is_up_to_date():
    result = updates.result_of(answer(tag_name="v0.1.0"), "0.1.0")
    assert result.newer is None and result.latest == "0.1.0" and result.error is None


@pytest.mark.parametrize("fields", [{"draft": True}, {"prerelease": True}, {"tag_name": "v0.3.0-beta"}, {"tag_name": 3}])
def test_drafts_and_pre_releases_are_never_offered(fields):
    result = updates.result_of(answer(**fields), "0.1.0")
    assert result.newer is None and result.error


def test_a_link_elsewhere_than_github_is_replaced_by_the_releases_page():
    result = updates.result_of(answer(html_url="https://example.com/evil"), "0.1.0")
    assert result.newer.url == updates.RELEASES_PAGE


@pytest.mark.parametrize("body", [None, b"", b"not json", b"[1, 2]", b"\xff\xfe"])
def test_offline_or_a_malformed_answer_is_an_error_not_an_update(body):
    result = updates.result_of(body, "0.1.0")
    assert result.newer is None and result.error


def test_the_checker_sends_what_it_gets_back(qapp_updates):
    checker = updates.UpdateChecker("0.1.0")
    asked, results = [], []
    checker._get = lambda url, done: (asked.append(url), done(answer()))
    checker.checked.connect(results.append)
    checker.check()
    assert asked == [updates.LATEST_URL]
    assert results[0].newer.version == "0.2.0" and not checker.busy


def test_a_checkout_without_a_version_never_asks(qapp_updates):
    checker = updates.UpdateChecker("unknown")
    asked, results = [], []
    checker._get = lambda url, done: asked.append(url)
    checker.checked.connect(results.append)
    checker.check()
    assert asked == [] and results[0].error


@pytest.fixture
def qapp_updates():
    from PyQt6.QtCore import QCoreApplication

    return QCoreApplication.instance() or QCoreApplication([])
