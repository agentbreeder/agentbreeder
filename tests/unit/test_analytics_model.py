from api.models.database import AnalyticsEvent


def test_analytics_event_table_name():
    assert AnalyticsEvent.__tablename__ == "analytics_events"
