from __future__ import annotations


def test_tmdb_metrics_recorder_lives_outside_helper():
    from movies.tmdb_metrics import TMDbMetricsRecorder

    assert TMDbMetricsRecorder.__module__ == "movies.tmdb_metrics"


def test_tmdb_helper_owns_metrics_recorder_collaborator():
    from movies.tmdb_client import TMDbHelper
    from movies.tmdb_metrics import TMDbMetricsRecorder

    helper = TMDbHelper(api_key="test")

    assert isinstance(helper._metrics_recorder, TMDbMetricsRecorder)
