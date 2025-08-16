#!/usr/bin/env python3
"""
Enhanced Error Handling Components for NextReel
===============================================
Complete implementation of error handling with circuit breakers, retry logic,
and comprehensive exception management.

INSTRUCTIONS FOR CLAUDE CODE:
1. Save this file as 'error_handling.py' in your project root
2. Update your settings.py to import and use this CircuitBreaker
3. Import these components in app.py and other modules as needed
4. Run the test again to verify all error handling works

This module provides:
- Enhanced CircuitBreaker with async support
- Retry decorator with exponential backoff
- Global error handlers for the application
- Custom exception classes for different error types
- Error recovery strategies
"""

import asyncio
import functools
import logging
import time
import traceback
from datetime import datetime, timedelta
from enum import Enum
from typing import Optional, Callable, Any, Dict, List, Type
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


class CircuitState(Enum):
    """Circuit breaker states"""
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


@dataclass
class CircuitBreakerConfig:
    """Configuration for circuit breaker"""
    failure_threshold: int = 5
    recovery_timeout: int = 60
    expected_exception: Type[Exception] = Exception
    success_threshold: int = 2  # Successes needed to close from half-open
    name: str = "default"


class CircuitBreaker:
    """
    Enhanced Circuit Breaker implementation with async support
    
    This implementation matches the test requirements and provides
    production-ready error handling for database and API calls.
    """
    
    def __init__(self, 
                 failure_threshold: int = 5,
                 recovery_timeout: int = 60,
                 expected_exception: Type[Exception] = Exception,
                 success_threshold: int = 2,
                 name: str = "default"):
        """
        Initialize circuit breaker
        
        Args:
            failure_threshold: Number of failures before opening circuit
            recovery_timeout: Seconds to wait before attempting recovery
            expected_exception: Exception type to catch
            success_threshold: Successful calls needed to close from half-open
            name: Name for logging
        """
        self.failure_threshold = failure_threshold
        self.recovery_timeout = recovery_timeout
        self.expected_exception = expected_exception
        self.success_threshold = success_threshold
        self.name = name
        
        # State tracking
        self.state = CircuitState.CLOSED
        self.failure_count = 0
        self.success_count = 0
        self.last_failure_time: Optional[datetime] = None
        self.last_success_time: Optional[datetime] = None
        
        # Statistics
        self.total_calls = 0
        self.total_failures = 0
        self.total_successes = 0
        self.circuit_opens = 0
        
    def _should_attempt_reset(self) -> bool:
        """Check if enough time has passed to attempt reset"""
        if self.last_failure_time is None:
            return True
        
        elapsed = (datetime.now() - self.last_failure_time).total_seconds()
        return elapsed >= self.recovery_timeout
    
    def _record_success(self):
        """Record a successful call"""
        self.total_successes += 1
        self.last_success_time = datetime.now()
        
        if self.state == CircuitState.HALF_OPEN:
            self.success_count += 1
            if self.success_count >= self.success_threshold:
                self._close_circuit()
        elif self.state == CircuitState.CLOSED:
            self.failure_count = max(0, self.failure_count - 1)
    
    def _record_failure(self):
        """Record a failed call"""
        self.total_failures += 1
        self.failure_count += 1
        self.last_failure_time = datetime.now()
        
        if self.state == CircuitState.HALF_OPEN:
            self._open_circuit()
        elif self.failure_count >= self.failure_threshold:
            self._open_circuit()
    
    def _open_circuit(self):
        """Open the circuit breaker"""
        if self.state != CircuitState.OPEN:
            self.state = CircuitState.OPEN
            self.circuit_opens += 1
            self.success_count = 0
            logger.error(f"Circuit breaker '{self.name}' opened after {self.failure_count} failures")
    
    def _close_circuit(self):
        """Close the circuit breaker"""
        self.state = CircuitState.CLOSED
        self.failure_count = 0
        self.success_count = 0
        logger.info(f"Circuit breaker '{self.name}' closed")
    
    def _half_open_circuit(self):
        """Set circuit to half-open state"""
        self.state = CircuitState.HALF_OPEN
        self.success_count = 0
        logger.info(f"Circuit breaker '{self.name}' half-open, attempting recovery")
    
    async def call(self, func: Callable, *args, **kwargs) -> Any:
        """
        Call a function through the circuit breaker
        
        Args:
            func: Async function to call
            *args: Arguments for the function
            **kwargs: Keyword arguments for the function
            
        Returns:
            Result of the function call
            
        Raises:
            Exception: If circuit is open or function fails
        """
        self.total_calls += 1
        
        # Check circuit state
        if self.state == CircuitState.OPEN:
            if self._should_attempt_reset():
                self._half_open_circuit()
            else:
                raise Exception(f"Circuit breaker '{self.name}' is open")
        
        # Attempt the call
        try:
            # Support both sync and async functions
            if asyncio.iscoroutinefunction(func):
                result = await func(*args, **kwargs)
            else:
                result = func(*args, **kwargs)
            
            self._record_success()
            return result
            
        except self.expected_exception as e:
            self._record_failure()
            raise
        except Exception as e:
            # Don't record failure for unexpected exceptions
            logger.warning(f"Unexpected exception in circuit breaker '{self.name}': {e}")
            raise
    
    def get_state(self) -> str:
        """Get current circuit state"""
        return self.state.value
    
    def get_stats(self) -> Dict[str, Any]:
        """Get circuit breaker statistics"""
        return {
            'name': self.name,
            'state': self.state.value,
            'failure_count': self.failure_count,
            'success_count': self.success_count,
            'total_calls': self.total_calls,
            'total_failures': self.total_failures,
            'total_successes': self.total_successes,
            'circuit_opens': self.circuit_opens,
            'last_failure': self.last_failure_time.isoformat() if self.last_failure_time else None,
            'last_success': self.last_success_time.isoformat() if self.last_success_time else None
        }
    
    def reset(self):
        """Manually reset the circuit breaker"""
        self._close_circuit()
        logger.info(f"Circuit breaker '{self.name}' manually reset")


class RetryPolicy:
    """Retry policy with exponential backoff"""
    
    def __init__(self,
                 max_retries: int = 3,
                 base_delay: float = 1.0,
                 max_delay: float = 60.0,
                 exponential_base: float = 2.0,
                 jitter: bool = True):
        """
        Initialize retry policy
        
        Args:
            max_retries: Maximum number of retry attempts
            base_delay: Initial delay between retries (seconds)
            max_delay: Maximum delay between retries (seconds)
            exponential_base: Base for exponential backoff
            jitter: Add random jitter to delays
        """
        self.max_retries = max_retries
        self.base_delay = base_delay
        self.max_delay = max_delay
        self.exponential_base = exponential_base
        self.jitter = jitter
    
    def calculate_delay(self, attempt: int) -> float:
        """Calculate delay for given attempt number"""
        delay = min(
            self.base_delay * (self.exponential_base ** attempt),
            self.max_delay
        )
        
        if self.jitter:
            import random
            delay *= (0.5 + random.random())
        
        return delay


def retry_with_backoff(
    max_retries: int = 3,
    base_delay: float = 1.0,
    max_delay: float = 60.0,
    exceptions: tuple = (Exception,)
):
    """
    Decorator for retrying functions with exponential backoff
    
    Args:
        max_retries: Maximum number of retry attempts
        base_delay: Initial delay between retries
        max_delay: Maximum delay between retries
        exceptions: Tuple of exceptions to catch and retry
    """
    def decorator(func):
        policy = RetryPolicy(max_retries, base_delay, max_delay)
        
        @functools.wraps(func)
        async def async_wrapper(*args, **kwargs):
            last_exception = None
            
            for attempt in range(max_retries + 1):
                try:
                    return await func(*args, **kwargs)
                except exceptions as e:
                    last_exception = e
                    
                    if attempt < max_retries:
                        delay = policy.calculate_delay(attempt)
                        logger.warning(
                            f"Retry {attempt + 1}/{max_retries} for {func.__name__} "
                            f"after {delay:.2f}s delay. Error: {e}"
                        )
                        await asyncio.sleep(delay)
                    else:
                        logger.error(
                            f"All {max_retries} retries failed for {func.__name__}. "
                            f"Last error: {e}"
                        )
            
            raise last_exception
        
        @functools.wraps(func)
        def sync_wrapper(*args, **kwargs):
            last_exception = None
            
            for attempt in range(max_retries + 1):
                try:
                    return func(*args, **kwargs)
                except exceptions as e:
                    last_exception = e
                    
                    if attempt < max_retries:
                        delay = policy.calculate_delay(attempt)
                        logger.warning(
                            f"Retry {attempt + 1}/{max_retries} for {func.__name__} "
                            f"after {delay:.2f}s delay. Error: {e}"
                        )
                        time.sleep(delay)
                    else:
                        logger.error(
                            f"All {max_retries} retries failed for {func.__name__}. "
                            f"Last error: {e}"
                        )
            
            raise last_exception
        
        if asyncio.iscoroutinefunction(func):
            return async_wrapper
        else:
            return sync_wrapper
    
    return decorator


# Custom Exception Classes
class NextReelException(Exception):
    """Base exception for NextReel application"""
    pass


class DatabaseException(NextReelException):
    """Database-related exceptions"""
    pass


class APIException(NextReelException):
    """External API exceptions"""
    pass


class ValidationException(NextReelException):
    """Data validation exceptions"""
    pass


class RateLimitException(NextReelException):
    """Rate limiting exceptions"""
    pass


class AuthenticationException(NextReelException):
    """Authentication/authorization exceptions"""
    pass


# Global Error Handler Registry
class ErrorHandlerRegistry:
    """Registry for global error handlers"""
    
    def __init__(self):
        self.handlers: Dict[Type[Exception], List[Callable]] = {}
        self.default_handler: Optional[Callable] = None
    
    def register(self, exception_type: Type[Exception], handler: Callable):
        """Register an error handler for specific exception type"""
        if exception_type not in self.handlers:
            self.handlers[exception_type] = []
        self.handlers[exception_type].append(handler)
    
    def set_default_handler(self, handler: Callable):
        """Set default handler for unhandled exceptions"""
        self.default_handler = handler
    
    async def handle_error(self, error: Exception, context: Dict[str, Any] = None):
        """Handle an error using registered handlers"""
        context = context or {}
        
        # Find handlers for this exception type
        handlers = []
        for exc_type, exc_handlers in self.handlers.items():
            if isinstance(error, exc_type):
                handlers.extend(exc_handlers)
        
        # Execute handlers
        if handlers:
            for handler in handlers:
                try:
                    if asyncio.iscoroutinefunction(handler):
                        await handler(error, context)
                    else:
                        handler(error, context)
                except Exception as e:
                    logger.error(f"Error in error handler: {e}")
        elif self.default_handler:
            try:
                if asyncio.iscoroutinefunction(self.default_handler):
                    await self.default_handler(error, context)
                else:
                    self.default_handler(error, context)
            except Exception as e:
                logger.error(f"Error in default handler: {e}")
        else:
            # No handler found, log the error
            logger.error(f"Unhandled error: {error}\nContext: {context}")


# Create global error handler registry
error_registry = ErrorHandlerRegistry()


# Error Recovery Strategies
class RecoveryStrategy:
    """Base class for error recovery strategies"""
    
    async def recover(self, error: Exception, context: Dict[str, Any]):
        """Attempt to recover from an error"""
        raise NotImplementedError


class DatabaseRecoveryStrategy(RecoveryStrategy):
    """Recovery strategy for database errors"""
    
    def __init__(self, db_pool):
        self.db_pool = db_pool
    
    async def recover(self, error: Exception, context: Dict[str, Any]):
        """Attempt to recover from database error"""
        logger.info("Attempting database recovery...")
        
        # Close and reinitialize pool
        try:
            await self.db_pool.close_pool()
            await asyncio.sleep(2)
            await self.db_pool.init_pool()
            logger.info("Database pool reinitialized successfully")
            return True
        except Exception as e:
            logger.error(f"Database recovery failed: {e}")
            return False


class CacheRecoveryStrategy(RecoveryStrategy):
    """Recovery strategy for cache errors"""
    
    def __init__(self, redis_client):
        self.redis_client = redis_client
    
    async def recover(self, error: Exception, context: Dict[str, Any]):
        """Attempt to recover from cache error"""
        logger.info("Attempting cache recovery...")
        
        try:
            # Try to reconnect to Redis
            await self.redis_client.ping()
            logger.info("Redis connection restored")
            return True
        except Exception as e:
            logger.warning(f"Cache recovery failed, operating without cache: {e}")
            # Continue without cache
            return False


# Application Error Manager
class ErrorManager:
    """Central error management for the application"""
    
    def __init__(self):
        self.circuit_breakers: Dict[str, CircuitBreaker] = {}
        self.recovery_strategies: Dict[str, RecoveryStrategy] = {}
        self.error_counts: Dict[str, int] = {}
        self.last_errors: Dict[str, Dict[str, Any]] = {}
    
    def add_circuit_breaker(self, name: str, circuit_breaker: CircuitBreaker):
        """Add a circuit breaker to the manager"""
        self.circuit_breakers[name] = circuit_breaker
    
    def add_recovery_strategy(self, name: str, strategy: RecoveryStrategy):
        """Add a recovery strategy"""
        self.recovery_strategies[name] = strategy
    
    def get_circuit_breaker(self, name: str) -> Optional[CircuitBreaker]:
        """Get a circuit breaker by name"""
        return self.circuit_breakers.get(name)
    
    async def handle_error(self, 
                          error: Exception,
                          component: str,
                          context: Dict[str, Any] = None,
                          attempt_recovery: bool = True):
        """
        Handle an error with optional recovery
        
        Args:
            error: The exception that occurred
            component: Component where error occurred
            context: Additional context information
            attempt_recovery: Whether to attempt recovery
        """
        context = context or {}
        
        # Track error
        self.error_counts[component] = self.error_counts.get(component, 0) + 1
        self.last_errors[component] = {
            'error': str(error),
            'type': type(error).__name__,
            'timestamp': datetime.now().isoformat(),
            'context': context
        }
        
        # Log error with context
        logger.error(
            f"Error in {component}: {error}\n"
            f"Context: {context}\n"
            f"Traceback: {traceback.format_exc()}"
        )
        
        # Handle through registry
        await error_registry.handle_error(error, {**context, 'component': component})
        
        # Attempt recovery if configured
        if attempt_recovery and component in self.recovery_strategies:
            strategy = self.recovery_strategies[component]
            try:
                success = await strategy.recover(error, context)
                if success:
                    logger.info(f"Recovery successful for {component}")
                    self.error_counts[component] = 0
                else:
                    logger.warning(f"Recovery failed for {component}")
            except Exception as e:
                logger.error(f"Error during recovery for {component}: {e}")
    
    def get_error_stats(self) -> Dict[str, Any]:
        """Get error statistics"""
        stats = {
            'error_counts': self.error_counts,
            'last_errors': self.last_errors,
            'circuit_breakers': {}
        }
        
        for name, cb in self.circuit_breakers.items():
            stats['circuit_breakers'][name] = cb.get_stats()
        
        return stats
    
    def reset_component(self, component: str):
        """Reset error state for a component"""
        if component in self.error_counts:
            self.error_counts[component] = 0
        if component in self.last_errors:
            del self.last_errors[component]
        if component in self.circuit_breakers:
            self.circuit_breakers[component].reset()


# Create global error manager
error_manager = ErrorManager()


# Utility functions for error handling
def setup_error_handling(app):
    """
    Set up error handling for a Quart application
    
    Args:
        app: Quart application instance
    """
    
    @app.errorhandler(404)
    async def not_found_error(error):
        """Handle 404 errors"""
        return {'error': 'Resource not found'}, 404
    
    @app.errorhandler(500)
    async def internal_error(error):
        """Handle 500 errors"""
        await error_manager.handle_error(
            error,
            'app',
            {'path': request.path if 'request' in globals() else 'unknown'}
        )
        return {'error': 'Internal server error'}, 500
    
    @app.errorhandler(DatabaseException)
    async def database_error(error):
        """Handle database errors"""
        await error_manager.handle_error(error, 'database')
        return {'error': 'Database error occurred'}, 503
    
    @app.errorhandler(APIException)
    async def api_error(error):
        """Handle API errors"""
        await error_manager.handle_error(error, 'api')
        return {'error': 'External API error'}, 502
    
    @app.errorhandler(RateLimitException)
    async def rate_limit_error(error):
        """Handle rate limit errors"""
        return {'error': 'Rate limit exceeded'}, 429
    
    @app.errorhandler(AuthenticationException)
    async def auth_error(error):
        """Handle authentication errors"""
        return {'error': 'Authentication failed'}, 401
    
    @app.errorhandler(ValidationException)
    async def validation_error(error):
        """Handle validation errors"""
        return {'error': str(error)}, 400
    
    # Set up default error handler
    async def default_error_handler(error, context):
        """Default error handler"""
        logger.error(f"Unhandled error: {error}, Context: {context}")
    
    error_registry.set_default_handler(default_error_handler)
    
    logger.info("Error handling configured for application")


# Example usage in your application
if __name__ == "__main__":
    """Example usage of error handling components"""
    
    async def example_usage():
        # Create circuit breaker for database
        db_circuit = CircuitBreaker(
            failure_threshold=3,
            recovery_timeout=30,
            expected_exception=DatabaseException,
            name="database"
        )
        
        # Add to error manager
        error_manager.add_circuit_breaker("database", db_circuit)
        
        # Example function with circuit breaker
        async def database_query():
            # Simulate database operation
            import random
            if random.random() < 0.3:  # 30% chance of failure
                raise DatabaseException("Connection failed")
            return "Query successful"
        
        # Use circuit breaker
        try:
            result = await db_circuit.call(database_query)
            print(f"Result: {result}")
        except Exception as e:
            print(f"Error: {e}")
        
        # Get statistics
        stats = db_circuit.get_stats()
        print(f"Circuit breaker stats: {stats}")
        
        # Example with retry decorator
        @retry_with_backoff(max_retries=3, exceptions=(APIException,))
        async def api_call():
            # Simulate API call
            import random
            if random.random() < 0.5:  # 50% chance of failure
                raise APIException("API timeout")
            return "API response"
        
        try:
            result = await api_call()
            print(f"API Result: {result}")
        except Exception as e:
            print(f"API Error after retries: {e}")
    
    # Run example
    import asyncio
    asyncio.run(example_usage())