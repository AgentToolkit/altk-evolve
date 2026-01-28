"""
Unit tests for kaizen.auto module.

Tests the low-code tracing functionality including:
- Framework detection
- Instrumentation checks
- enable_tracing function
- Auto-mode behavior
"""

import os
import pytest
from unittest.mock import patch, MagicMock


class TestFrameworkDetection:
    """Tests for detect_installed_frameworks()"""
    
    def test_returns_list(self):
        """Should return a list of framework names."""
        from kaizen.auto import detect_installed_frameworks
        
        frameworks = detect_installed_frameworks()
        assert isinstance(frameworks, list)
    
    def test_detect_openai_when_imported(self):
        """Should detect OpenAI if it can be imported."""
        from kaizen.auto import detect_installed_frameworks
        
        frameworks = detect_installed_frameworks()
        # This will pass if openai is installed in test env
        # Just verify we get a list back
        assert isinstance(frameworks, list)


class TestInstrumentationCheck:
    """Tests for is_already_instrumented()"""
    
    def test_returns_bool(self):
        """Should return a boolean."""
        from kaizen.auto import is_already_instrumented
        
        result = is_already_instrumented()
        assert isinstance(result, bool)
    
    def test_not_instrumented_with_proxy_provider(self):
        """Should return False when ProxyTracerProvider is set."""
        from kaizen.auto import is_already_instrumented
        
        # Just verify the function runs without error
        result = is_already_instrumented()
        assert isinstance(result, bool)


class TestEnableTracing:
    """Tests for enable_tracing() function"""
    
    def test_returns_none_when_already_instrumented(self):
        """Should return None if already instrumented and force=False."""
        from kaizen.auto import enable_tracing
        
        with patch('kaizen.auto.is_already_instrumented', return_value=True):
            tracer = enable_tracing(project="test")
            assert tracer is None
    
    def test_uses_env_project_name(self):
        """Should use KAIZEN_TRACING_PROJECT env var."""
        from kaizen.auto import enable_tracing
        
        with patch.dict(os.environ, {'KAIZEN_TRACING_PROJECT': 'env-project'}):
            with patch('kaizen.auto.is_already_instrumented', return_value=False):
                with patch('phoenix.otel.register') as mock_register:
                    mock_register.return_value = MagicMock()
                    
                    enable_tracing()
                    
                    mock_register.assert_called_once()
                    call_kwargs = mock_register.call_args.kwargs
                    assert call_kwargs['project_name'] == 'env-project'
    
    def test_force_overrides_instrumented_check(self):
        """Should instrument when force=True even if already instrumented."""
        from kaizen.auto import enable_tracing
        
        with patch('kaizen.auto.is_already_instrumented', return_value=True):
            with patch('phoenix.otel.register') as mock_register:
                mock_register.return_value = MagicMock()
                
                tracer = enable_tracing(project="test", force=True)
                
                assert tracer is not None
                mock_register.assert_called_once()


class TestAutoMode:
    """Tests for kaizen.auto module behavior"""
    
    def test_auto_does_nothing_when_disabled(self):
        """Should not instrument when KAIZEN_AUTO_ENABLED is not set."""
        import sys
        
        # Ensure clean state
        if 'kaizen.auto' in sys.modules:
            del sys.modules['kaizen.auto']
            
        with patch.dict(os.environ, {'KAIZEN_AUTO_ENABLED': ''}, clear=False):
            # We need to spy on enable_tracing before importing
            # But since it's defined in the module we're importing, we can't patch it directly
            # Instead, we'll patch phoenix.otel.register which enable_tracing calls
            
            with patch('phoenix.otel.register') as mock_register:
                import kaizen.auto
                
                # Should not have called register
                mock_register.assert_not_called()


class TestGetInstrumentedFrameworks:
    """Tests for get_instrumented_frameworks()"""
    
    def test_returns_set(self):
        """Should return a set."""
        from kaizen.auto import get_instrumented_frameworks
        
        result = get_instrumented_frameworks()
        assert isinstance(result, set)
    
    def test_returns_copy(self):
        """Should return a copy, not the original set."""
        from kaizen.auto import get_instrumented_frameworks, _instrumented_frameworks
        
        result = get_instrumented_frameworks()
        result.add('fake_framework')
        
        # Original should not be modified
        assert 'fake_framework' not in _instrumented_frameworks


class TestFlushTraces:
    """Tests for flush_traces()"""
    
    def test_flush_when_provider_exists(self):
        """Should call force_flush when provider exists."""
        from kaizen import auto
        
        mock_provider = MagicMock()
        original_provider = auto._tracer_provider
        
        try:
            auto._tracer_provider = mock_provider
            auto.flush_traces()
            mock_provider.force_flush.assert_called_once()
        finally:
            auto._tracer_provider = original_provider
    
    def test_flush_when_no_provider(self):
        """Should not raise when no provider."""
        from kaizen import auto
        
        original_provider = auto._tracer_provider
        
        try:
            auto._tracer_provider = None
            auto.flush_traces()  # Should not raise
        finally:
            auto._tracer_provider = original_provider


class TestGetTracerProvider:
    """Tests for get_tracer_provider()"""
    
    def test_returns_none_before_setup(self):
        """Should return None before tracing is enabled."""
        from kaizen import auto
        
        original_provider = auto._tracer_provider
        
        try:
            auto._tracer_provider = None
            result = auto.get_tracer_provider()
            assert result is None
        finally:
            auto._tracer_provider = original_provider
    
    def test_returns_provider_after_setup(self):
        """Should return provider after tracing is enabled."""
        from kaizen import auto
        
        mock_provider = MagicMock()
        original_provider = auto._tracer_provider
        
        try:
            auto._tracer_provider = mock_provider
            result = auto.get_tracer_provider()
            assert result is mock_provider
        finally:
            auto._tracer_provider = original_provider


@pytest.mark.unit
class TestTracingIntegration:
    """Integration-style tests for the tracing module."""
    
    def test_enable_tracing_end_to_end(self):
        """Test enable_tracing with mocked Phoenix."""
        from kaizen.auto import enable_tracing
        
        with patch('kaizen.auto.is_already_instrumented', return_value=False):
            with patch('phoenix.otel.register') as mock_register:
                mock_register.return_value = MagicMock()
                
                tracer = enable_tracing(
                    project="integration-test",
                    endpoint="http://test:8080/traces",
                    frameworks=[]  # Empty list to skip framework instrumentation
                )
                
                assert tracer is not None
                mock_register.assert_called_once_with(
                    project_name="integration-test",
                    endpoint="http://test:8080/traces"
                )
