from unittest.mock import patch



def test_tool_search_context_resolution_uses_model_config_override():
    """Tool schema assembly must not probe cloud metadata when config pins context."""
    import model_tools

    cfg = {
        "model": {
            "provider": "custom:kai-local-openai",
            "default": "kai-local",
            "base_url": "http://127.0.0.1:8080/v1",
            "context_length": 64000,
        },
        "providers": {
            "kai-local-openai": {
                "name": "Kai local OpenAI-compatible endpoint",
                "base_url": "http://127.0.0.1:8080/v1",
                "default_model": "kai-local",
            }
        },
    }

    with patch("hermes_cli.config.load_config", return_value=cfg), \
         patch("agent.model_metadata.get_model_context_length", return_value=64000) as get_ctx:
        assert model_tools._resolve_active_context_length() == 64000

    get_ctx.assert_called_once()
    _, kwargs = get_ctx.call_args
    assert kwargs["base_url"] == "http://127.0.0.1:8080/v1"
    assert kwargs["provider"] == "custom"
    assert kwargs["config_context_length"] == 64000
    assert kwargs["custom_providers"][0]["base_url"] == "http://127.0.0.1:8080/v1"
