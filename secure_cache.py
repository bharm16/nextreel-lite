import asyncio
import hashlib
import hmac
import json
import time
import base64
from typing import Optional, Dict, Any, List, Set, Union
from datetime import datetime, timedelta
from dataclasses import dataclass, field
from enum import Enum
import redis.asyncio as redis
from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
import logging
from collections import defaultdict, deque

logger = logging.getLogger(__name__)

class CacheNamespace(Enum):
    """Cache namespaces for different data types"""
    SESSION = "session"
    MOVIE = "movie"
    USER = "user"
    QUEUE = "queue"
    API = "api"
    TEMP = "temp"

@dataclass
class CachePolicy:
    """Cache policy configuration"""
    namespace: CacheNamespace
    ttl: int  # Time to live in seconds
    max_size: Optional[int] = None  # Max items in namespace
    encrypt: bool = False  # Whether to encrypt values
    compress: bool = False  # Whether to compress values
    version: int = 1  # Cache version for invalidation
    refresh_on_access: bool = False  # Extend TTL on access
    
    # Security settings
    require_signature: bool = True  # Require HMAC signature
    max_key_length: int = 250  # Max Redis key length
    allowed_key_pattern: Optional[str] = None  # Regex pattern for keys

@dataclass
class CacheMetrics:
    """Cache performance and security metrics"""
    hits: int = 0
    misses: int = 0
    sets: int = 0
    deletes: int = 0
    errors: int = 0
    
    # Security metrics
    invalid_signatures: int = 0
    poisoning_attempts: int = 0
    encryption_failures: int = 0
    
    # Performance metrics
    avg_get_time: float = 0.0
    avg_set_time: float = 0.0
    memory_used: int = 0
    
    # Rate limiting
    rate_limit_hits: int = 0
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            'hits': self.hits,
            'misses': self.misses,
            'hit_rate': self.hits / (self.hits + self.misses) if (self.hits + self.misses) > 0 else 0,
            'sets': self.sets,
            'deletes': self.deletes,
            'errors': self.errors,
            'security': {
                'invalid_signatures': self.invalid_signatures,
                'poisoning_attempts': self.poisoning_attempts,
                'encryption_failures': self.encryption_failures
            },
            'performance': {
                'avg_get_time_ms': self.avg_get_time * 1000,
                'avg_set_time_ms': self.avg_set_time * 1000,
                'memory_used_mb': self.memory_used / (1024 * 1024)
            },
            'rate_limit_hits': self.rate_limit_hits
        }

class SecureCacheManager:
    """Secure Redis cache manager with encryption and signing"""
    
    # Default cache policies
    DEFAULT_POLICIES = {
        CacheNamespace.SESSION: CachePolicy(
            namespace=CacheNamespace.SESSION,
            ttl=1800,  # 30 minutes
            encrypt=True,
            require_signature=True
        ),
        CacheNamespace.MOVIE: CachePolicy(
            namespace=CacheNamespace.MOVIE,
            ttl=7200,  # 2 hours
            encrypt=False,
            compress=True,
            max_size=10000
        ),
        CacheNamespace.USER: CachePolicy(
            namespace=CacheNamespace.USER,
            ttl=3600,  # 1 hour
            encrypt=True,
            require_signature=True
        ),
        CacheNamespace.QUEUE: CachePolicy(
            namespace=CacheNamespace.QUEUE,
            ttl=900,  # 15 minutes
            encrypt=False,
            refresh_on_access=True
        ),
        CacheNamespace.API: CachePolicy(
            namespace=CacheNamespace.API,
            ttl=300,  # 5 minutes
            encrypt=False,
            max_size=1000
        ),
        CacheNamespace.TEMP: CachePolicy(
            namespace=CacheNamespace.TEMP,
            ttl=60,  # 1 minute
            encrypt=False,
            require_signature=False
        )
    }
    
    def __init__(self, 
                 redis_url: str,
                 secret_key: str,
                 policies: Optional[Dict[CacheNamespace, CachePolicy]] = None,
                 enable_monitoring: bool = True):
        """
        Initialize secure cache manager
        
        Args:
            redis_url: Redis connection URL
            secret_key: Secret key for HMAC signing and encryption
            policies: Custom cache policies
            enable_monitoring: Enable metrics collection
        """
        self.redis_url = redis_url
        self.redis_client: Optional[redis.Redis] = None
        self.policies = policies or self.DEFAULT_POLICIES
        
        # Security keys
        self.signing_key = hashlib.sha256(f"{secret_key}:signing".encode()).digest()
        self.encryption_key = self._derive_encryption_key(secret_key)
        self.fernet = Fernet(self.encryption_key)
        
        # Metrics
        self.metrics = defaultdict(CacheMetrics)
        self.enable_monitoring = enable_monitoring
        
        # Rate limiting
        self.rate_limiters = {}
        self.max_ops_per_second = 100  # Global rate limit
        
        # Cache invalidation tracking
        self.invalidation_patterns: Dict[str, Set[str]] = defaultdict(set)
        
        # Security tracking
        self.suspicious_keys: deque = deque(maxlen=100)
        self.blocked_patterns: Set[str] = set()
        
        # Background tasks
        self._cleanup_task: Optional[asyncio.Task] = None
        self._monitor_task: Optional[asyncio.Task] = None
    
    def _derive_encryption_key(self, secret: str) -> bytes:
        """Derive encryption key from secret"""
        kdf = PBKDF2HMAC(
            algorithm=hashes.SHA256(),
            length=32,
            salt=b'nextreel-cache-v1',
            iterations=100000,
        )
        key = base64.urlsafe_b64encode(kdf.derive(secret.encode()))
        return key
    
    async def initialize(self):
        """Initialize Redis connection and start background tasks"""
        # Create Redis pool
        pool = redis.ConnectionPool.from_url(
            self.redis_url,
            max_connections=50,
            decode_responses=False,  # We handle encoding/decoding
            socket_connect_timeout=5,
            socket_timeout=5,
            retry_on_timeout=True,
            health_check_interval=30
        )
        self.redis_client = redis.Redis(connection_pool=pool)
        
        # Test connection
        await self.redis_client.ping()
        
        # Start background tasks
        if self.enable_monitoring:
            self._cleanup_task = asyncio.create_task(self._cleanup_loop())
            self._monitor_task = asyncio.create_task(self._monitor_loop())
        
        logger.info("Secure cache manager initialized")
    
    def _generate_cache_key(self, namespace: CacheNamespace, key: str) -> str:
        """Generate secure cache key with namespace and version"""
        policy = self.policies.get(namespace)
        if not policy:
            raise ValueError(f"No policy defined for namespace {namespace}")
        
        # Validate key length
        if len(key) > policy.max_key_length:
            raise ValueError(f"Key too long: {len(key)} > {policy.max_key_length}")
        
        # Validate key pattern if specified
        if policy.allowed_key_pattern:
            import re
            if not re.match(policy.allowed_key_pattern, key):
                self.metrics[namespace].poisoning_attempts += 1
                raise ValueError(f"Invalid key pattern: {key}")
        
        # Generate namespaced key with version
        cache_key = f"{namespace.value}:v{policy.version}:{key}"
        
        return cache_key
    
    def _sign_value(self, value: bytes) -> bytes:
        """Sign value with HMAC for integrity"""
        signature = hmac.new(self.signing_key, value, hashlib.sha256).digest()
        return signature + value
    
    def _verify_signature(self, signed_value: bytes) -> Optional[bytes]:
        """Verify HMAC signature and return value if valid"""
        if len(signed_value) < 32:
            return None
        
        signature = signed_value[:32]
        value = signed_value[32:]
        
        expected_signature = hmac.new(self.signing_key, value, hashlib.sha256).digest()
        
        if hmac.compare_digest(signature, expected_signature):
            return value
        
        return None
    
    def _encrypt_value(self, value: bytes) -> bytes:
        """Encrypt value using Fernet"""
        try:
            return self.fernet.encrypt(value)
        except Exception as e:
            self.metrics[CacheNamespace.SESSION].encryption_failures += 1
            logger.error(f"Encryption failed: {e}")
            raise
    
    def _decrypt_value(self, encrypted_value: bytes) -> Optional[bytes]:
        """Decrypt value using Fernet"""
        try:
            return self.fernet.decrypt(encrypted_value)
        except Exception as e:
            self.metrics[CacheNamespace.SESSION].encryption_failures += 1
            logger.error(f"Decryption failed: {e}")
            return None
    
    async def get(self, namespace: CacheNamespace, key: str, 
                  default: Any = None, extend_ttl: bool = False) -> Any:
        """
        Get value from cache with security checks
        
        Args:
            namespace: Cache namespace
            key: Cache key
            default: Default value if not found
            extend_ttl: Whether to extend TTL on access
        
        Returns:
            Cached value or default
        """
        if not self.redis_client:
            return default
        
        policy = self.policies.get(namespace)
        if not policy:
            return default
        
        start_time = time.time()
        
        try:
            # Generate cache key
            cache_key = self._generate_cache_key(namespace, key)
            
            # Get from Redis
            signed_value = await self.redis_client.get(cache_key)
            
            if not signed_value:
                self.metrics[namespace].misses += 1
                return default
            
            # Verify signature if required
            if policy.require_signature:
                value = self._verify_signature(signed_value)
                if not value:
                    self.metrics[namespace].invalid_signatures += 1
                    logger.warning(f"Invalid signature for key {cache_key}")
                    await self.redis_client.delete(cache_key)
                    return default
            else:
                value = signed_value
            
            # Decrypt if required
            if policy.encrypt:
                value = self._decrypt_value(value)
                if not value:
                    return default
            
            # Decompress if required
            if policy.compress:
                import zlib
                value = zlib.decompress(value)
            
            # Deserialize
            result = json.loads(value.decode())
            
            # Extend TTL if configured
            if extend_ttl or policy.refresh_on_access:
                await self.redis_client.expire(cache_key, policy.ttl)
            
            self.metrics[namespace].hits += 1
            
            # Update average get time
            elapsed = time.time() - start_time
            self.metrics[namespace].avg_get_time = (
                (self.metrics[namespace].avg_get_time * (self.metrics[namespace].hits - 1) + elapsed)
                / self.metrics[namespace].hits
            )
            
            return result
            
        except Exception as e:
            self.metrics[namespace].errors += 1
            logger.error(f"Cache get error for {namespace}:{key}: {e}")
            return default
    
    async def set(self, namespace: CacheNamespace, key: str, value: Any,
                  ttl: Optional[int] = None, if_not_exists: bool = False) -> bool:
        """
        Set value in cache with security
        
        Args:
            namespace: Cache namespace
            key: Cache key
            value: Value to cache
            ttl: Custom TTL (uses policy default if None)
            if_not_exists: Only set if key doesn't exist
        
        Returns:
            True if set successfully
        """
        if not self.redis_client:
            return False
        
        policy = self.policies.get(namespace)
        if not policy:
            return False
        
        start_time = time.time()
        
        try:
            # Check namespace size limit
            if policy.max_size:
                namespace_size = await self._get_namespace_size(namespace)
                if namespace_size >= policy.max_size:
                    # Evict oldest keys
                    await self._evict_oldest(namespace, count=10)
            
            # Generate cache key
            cache_key = self._generate_cache_key(namespace, key)
            
            # Serialize value
            serialized = json.dumps(value).encode()
            
            # Compress if required
            if policy.compress:
                import zlib
                serialized = zlib.compress(serialized, level=6)
            
            # Encrypt if required
            if policy.encrypt:
                serialized = self._encrypt_value(serialized)
            
            # Sign if required
            if policy.require_signature:
                serialized = self._sign_value(serialized)
            
            # Set in Redis
            cache_ttl = ttl or policy.ttl
            
            if if_not_exists:
                result = await self.redis_client.set(
                    cache_key, serialized, 
                    ex=cache_ttl, nx=True
                )
            else:
                result = await self.redis_client.setex(
                    cache_key, cache_ttl, serialized
                )
            
            if result:
                self.metrics[namespace].sets += 1
                
                # Update average set time
                elapsed = time.time() - start_time
                self.metrics[namespace].avg_set_time = (
                    (self.metrics[namespace].avg_set_time * (self.metrics[namespace].sets - 1) + elapsed)
                    / self.metrics[namespace].sets
                )
            
            return bool(result)
            
        except Exception as e:
            self.metrics[namespace].errors += 1
            logger.error(f"Cache set error for {namespace}:{key}: {e}")
            return False
    
    async def delete(self, namespace: CacheNamespace, key: str) -> bool:
        """Delete value from cache"""
        if not self.redis_client:
            return False
        
        try:
            cache_key = self._generate_cache_key(namespace, key)
            result = await self.redis_client.delete(cache_key)
            
            if result:
                self.metrics[namespace].deletes += 1
            
            return bool(result)
            
        except Exception as e:
            self.metrics[namespace].errors += 1
            logger.error(f"Cache delete error for {namespace}:{key}: {e}")
            return False
    
    async def delete_pattern(self, namespace: CacheNamespace, pattern: str) -> int:
        """Delete all keys matching pattern (use with caution)"""
        if not self.redis_client:
            return 0
        
        try:
            # Generate pattern with namespace
            cache_pattern = f"{namespace.value}:*:{pattern}"
            
            # Use SCAN to find keys (safer than KEYS)
            deleted = 0
            async for key in self.redis_client.scan_iter(match=cache_pattern, count=100):
                if await self.redis_client.delete(key):
                    deleted += 1
            
            self.metrics[namespace].deletes += deleted
            return deleted
            
        except Exception as e:
            logger.error(f"Pattern delete error for {namespace}:{pattern}: {e}")
            return 0
    
    async def invalidate_namespace(self, namespace: CacheNamespace):
        """Invalidate entire namespace by incrementing version"""
        if namespace in self.policies:
            self.policies[namespace].version += 1
            logger.info(f"Invalidated namespace {namespace}, new version: {self.policies[namespace].version}")
    
    async def add_invalidation_dependency(self, key: str, depends_on: str):
        """Add cache invalidation dependency"""
        self.invalidation_patterns[depends_on].add(key)
    
    async def invalidate_dependencies(self, key: str):
        """Invalidate all keys depending on this key"""
        if key in self.invalidation_patterns:
            for dependent_key in self.invalidation_patterns[key]:
                # Parse namespace from key
                parts = dependent_key.split(':', 2)
                if len(parts) >= 3:
                    namespace = CacheNamespace(parts[0])
                    actual_key = parts[2]
                    await self.delete(namespace, actual_key)
            
            del self.invalidation_patterns[key]
    
    async def _get_namespace_size(self, namespace: CacheNamespace) -> int:
        """Get number of keys in namespace"""
        if not self.redis_client:
            return 0
        
        pattern = f"{namespace.value}:*"
        count = 0
        
        async for _ in self.redis_client.scan_iter(match=pattern, count=100):
            count += 1
        
        return count
    
    async def _evict_oldest(self, namespace: CacheNamespace, count: int = 10):
        """Evict oldest keys from namespace"""
        if not self.redis_client:
            return
        
        pattern = f"{namespace.value}:*"
        keys_with_ttl = []
        
        # Get keys with TTL
        async for key in self.redis_client.scan_iter(match=pattern, count=100):
            ttl = await self.redis_client.ttl(key)
            if ttl > 0:
                keys_with_ttl.append((key, ttl))
        
        # Sort by TTL (ascending) and delete oldest
        keys_with_ttl.sort(key=lambda x: x[1])
        
        for key, _ in keys_with_ttl[:count]:
            await self.redis_client.delete(key)
            logger.debug(f"Evicted old key: {key}")
    
    async def _cleanup_loop(self):
        """Background task for cache cleanup"""
        while True:
            try:
                await asyncio.sleep(60)  # Run every minute
                
                # Clean up expired invalidation patterns
                now = time.time()
                expired = []
                for key in list(self.invalidation_patterns.keys()):
                    # Check if key still exists
                    if not await self.redis_client.exists(key):
                        expired.append(key)
                
                for key in expired:
                    del self.invalidation_patterns[key]
                
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Cleanup error: {e}")
    
    async def _monitor_loop(self):
        """Background task for monitoring"""
        while True:
            try:
                await asyncio.sleep(30)  # Run every 30 seconds
                
                if self.redis_client:
                    # Get Redis info
                    info = await self.redis_client.info('memory')
                    used_memory = info.get('used_memory', 0)
                    
                    # Update metrics
                    for namespace in self.metrics:
                        self.metrics[namespace].memory_used = used_memory // len(self.metrics)
                    
                    # Log suspicious activity
                    for namespace, metrics in self.metrics.items():
                        if metrics.invalid_signatures > 10:
                            logger.warning(f"High invalid signatures in {namespace}: {metrics.invalid_signatures}")
                        
                        if metrics.poisoning_attempts > 5:
                            logger.warning(f"Cache poisoning attempts in {namespace}: {metrics.poisoning_attempts}")
                
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Monitor error: {e}")
    
    async def get_metrics(self) -> Dict[str, Any]:
        """Get cache metrics"""
        return {
            namespace.value: metrics.to_dict()
            for namespace, metrics in self.metrics.items()
        }
    
    async def close(self):
        """Close cache manager and cleanup"""
        # Cancel background tasks
        if self._cleanup_task:
            self._cleanup_task.cancel()
        if self._monitor_task:
            self._monitor_task.cancel()
        
        # Close Redis connection
        if self.redis_client:
            await self.redis_client.close()
        
        logger.info("Secure cache manager closed")