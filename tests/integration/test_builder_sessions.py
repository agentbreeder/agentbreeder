def test_builder_session_model_importable():
    from api.models.database import BuilderSession

    assert BuilderSession.__tablename__ == "builder_sessions"
