# Optimized MovieQueue Implementation for Nextreel
# 

#


import asyncio
import logging
from logging_config import get_logger
import os
import time
import traceback
import json
from typing import Dict, Any, Optional, List, Set
from dataclasses import dataclass

import httpx
import redis.asyncio as redis
from quart import current_app

from scripts.movie import Movie
from scripts.filter_backend import ImdbRandomMovieFetcher
from .interfaces import MovieFetcher
from settings import DatabaseConnectionPool

logger = get_logger(__name__)
parent_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(parent_dir)

@dataclass
class MovieMetadata:
    """Lightweight movie metadata for queue storage"""
    tconst: str
    title: Optional[str] = None
    year: Optional[int] = None
    rating: Optional[float] = None
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            'tconst': self.tconst,
            'title': self.title,
            'year': self.year,
            'rating': self.rating
        }
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'MovieMetadata':
        return cls(**data)


class OptimizedMovieQueue:
    """Optimized movie queue manager with caching and lazy loading"""
    
    # Class-level HTTP client for connection pooling
    _http_client: Optional[httpx.AsyncClient] = None
    _redis_client: Optional[redis.Redis] = None
    
    def __init__(
        self, 
        db_pool: DatabaseConnectionPool, 
        movie_fetcher: MovieFetcher, 
        queue_size: int = 5,           # Reduced from 10 - less memory, faster loads
        prefetch_threshold: int = 2,    # Reduced from 3 - more responsive
        batch_size: int = 3,            # Reduced from 5 - smaller batches
        cache_ttl: int = 7200           # Increased from 3600 - 2 hour cache
    ):
        self.db_pool = db_pool
        self.movie_fetcher = movie_fetcher
        self.queue_size = queue_size
        self.prefetch_threshold = prefetch_threshold
        self.batch_size = batch_size
        self.cache_ttl = cache_ttl
        
        # Use more granular locks
        self.queue_locks = {}  # Per-user queue locks
        self.global_lock = asyncio.Lock()  # Only for user creation
        
        self.movie_enqueue_count = 0
        self.user_queues = {}
        self.stop_flags = {}
        
        # Performance metrics
        self.metrics = {
            'api_calls': 0,
            'cache_hits': 0,
            'cache_misses': 0,
            'queue_refills': 0
        }
    
    @classmethod
    async def get_http_client(cls) -> httpx.AsyncClient:
        """Get or create shared HTTP client with connection pooling"""
        if cls._http_client is None or cls._http_client.is_closed:
            cls._http_client = httpx.AsyncClient(
                timeout=httpx.Timeout(10.0, connect=5.0),
                limits=httpx.Limits(
                    max_keepalive_connections=20,
                    max_connections=50,
                    keepalive_expiry=30
                )
            )
        return cls._http_client
    
    @classmethod
    async def get_redis_client(cls) -> redis.Redis:
        """Get or create shared Redis client with optimized connection pool"""
        if cls._redis_client is None:
            # Use environment variable or config for Redis URL
            redis_url = os.getenv('UPSTASH_REDIS_URL', 'redis://localhost:6379')
            
            # Create connection pool with optimized settings
            pool = redis.ConnectionPool.from_url(
                redis_url,
                max_connections=50,
                socket_connect_timeout=5,
                socket_timeout=5,
                retry_on_timeout=True,
                health_check_interval=30,
                decode_responses=True
            )
            
            cls._redis_client = redis.Redis(connection_pool=pool)
        return cls._redis_client
    
    async def get_user_lock(self, user_id: str) -> asyncio.Lock:
        """Get or create per-user lock"""
        if user_id not in self.queue_locks:
            self.queue_locks[user_id] = asyncio.Lock()
        return self.queue_locks[user_id]
    
    async def get_cached_movie_data(self, tconst: str) -> Optional[Dict[str, Any]]:
        """Get movie data from Redis cache"""
        try:
            redis_client = await self.get_redis_client()
            cache_key = f"movie:{tconst}"
            cached = await redis_client.get(cache_key)
            
            if cached:
                self.metrics['cache_hits'] += 1
                logger.debug(f"Cache hit for movie {tconst}")
                return json.loads(cached)
            
            self.metrics['cache_misses'] += 1
            return None
            
        except Exception as e:
            logger.warning(f"Redis cache error for {tconst}: {e}")
            return None
    
    async def cache_movie_data(self, tconst: str, data: Dict[str, Any]):
        """Cache movie data in Redis"""
        try:
            redis_client = await self.get_redis_client()
            cache_key = f"movie:{tconst}"
            await redis_client.setex(
                cache_key,
                self.cache_ttl,
                json.dumps(data)
            )
            logger.debug(f"Cached movie data for {tconst}")
        except Exception as e:
            logger.warning(f"Failed to cache movie {tconst}: {e}")
    
    async def fetch_movie_data_with_cache(self, tconst: str) -> Optional[Dict[str, Any]]:
        """Fetch movie data with caching layer"""
        # Try cache first
        cached_data = await self.get_cached_movie_data(tconst)
        if cached_data:
            return cached_data
        
        # Fetch from API
        try:
            movie = Movie(tconst, self.db_pool)
            movie_data = await movie.get_movie_data()
            
            if movie_data:
                # Cache the result
                await self.cache_movie_data(tconst, movie_data)
                self.metrics['api_calls'] += 1
            
            return movie_data
            
        except Exception as e:
            logger.error(f"Error fetching movie {tconst}: {e}")
            return None
    
    async def get_user_queue(self, user_id: str) -> asyncio.Queue:
        """Get or create user queue with metadata objects"""
        if user_id not in self.user_queues:
            async with self.global_lock:
                if user_id not in self.user_queues:  # Double check
                    self.user_queues[user_id] = {
                        "queue": asyncio.Queue(maxsize=self.queue_size),
                        "criteria": {},
                        "seen_tconsts": set(),
                        "queued_tconsts": set(),
                        "populate_task": None
                    }
        return self.user_queues[user_id]["queue"]
    
    async def batch_fetch_movies(self, tconsts: List[str]) -> List[Dict[str, Any]]:
        """Fetch multiple movies concurrently with rate limiting"""
        semaphore = asyncio.Semaphore(3)  # Limit concurrent API calls
        
        async def fetch_with_limit(tconst: str):
            async with semaphore:
                return await self.fetch_movie_data_with_cache(tconst)
        
        tasks = [fetch_with_limit(tconst) for tconst in tconsts]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        
        # Filter out errors and None results
        valid_results = []
        for result, tconst in zip(results, tconsts):
            if isinstance(result, Exception):
                logger.error(f"Error fetching {tconst}: {result}")
            elif result:
                valid_results.append(result)
        
        return valid_results
    
    async def load_movies_into_queue(self, user_id: str):
        """Optimized movie loading with lazy fetching"""
        start_time = time.time()
        
        try:
            user_info = self.user_queues.get(user_id, {})
            criteria = user_info.get("criteria", {})
            user_queue = await self.get_user_queue(user_id)
            
            # Calculate how many movies we need
            current_size = user_queue.qsize()
            needed = min(self.batch_size, self.queue_size - current_size)
            
            if needed <= 0:
                logger.debug(f"Queue for {user_id} is full, skipping load")
                return
            
            logger.debug(f"Loading {needed} movies for user {user_id}")
            
            # Fetch movie IDs from database
            rows = await self.movie_fetcher.fetch_random_movies(criteria, needed * 2)  # Get extra for filtering
            
            if not rows:
                logger.warning(f"No movies found for criteria: {criteria}")
                return
            
            # Filter out seen and already queued movies
            seen = user_info.get("seen_tconsts", set())
            queued = user_info.get("queued_tconsts", set())
            unseen_tconsts = [
                row["tconst"] for row in rows 
                if row["tconst"] not in seen and row["tconst"] not in queued
            ][:needed]
            
            if not unseen_tconsts:
                logger.info(f"All fetched movies already seen for user {user_id}")
                return
            
            # Store only metadata in queue, not full movie data
            user_lock = await self.get_user_lock(user_id)
            async with user_lock:
                queued = user_info.setdefault("queued_tconsts", set())
                for tconst in unseen_tconsts:
                    if not user_queue.full():
                        # Store lightweight metadata
                        metadata = MovieMetadata(tconst=tconst)
                        await user_queue.put(metadata)
                        queued.add(tconst)
                        self.movie_enqueue_count += 1
                        logger.debug(f"Enqueued movie {tconst} for user {user_id}")
            
            self.metrics['queue_refills'] += 1
            
        except Exception as e:
            logger.error(f"Error loading movies for user {user_id}: {e}", exc_info=True)
        
        finally:
            elapsed = time.time() - start_time
            logger.info(
                f"Loaded movies for {user_id} in {elapsed:.2f}s "
                f"(cache hits: {self.metrics['cache_hits']}, "
                f"misses: {self.metrics['cache_misses']})"
            )
    
    async def dequeue_movie(self, user_id: str) -> Optional[Dict[str, Any]]:
        """Dequeue movie and fetch full data on demand"""
        user_queue = await self.get_user_queue(user_id)
        
        try:
            # Get metadata from queue
            metadata = await asyncio.wait_for(user_queue.get(), timeout=1.0)
            
            # Update tracking sets
            user_info = self.user_queues.get(user_id, {})
            seen = user_info.setdefault("seen_tconsts", set())
            queued = user_info.get("queued_tconsts", set())
            
            # Move from queued to seen
            seen.add(metadata.tconst)
            queued.discard(metadata.tconst)
            
            # Fetch full movie data (will use cache if available)
            movie_data = await self.fetch_movie_data_with_cache(metadata.tconst)
            
            # Trigger refill if needed
            if user_queue.qsize() <= self.prefetch_threshold:
                asyncio.create_task(self.load_movies_into_queue(user_id))
            
            return movie_data
            
        except asyncio.TimeoutError:
            logger.warning(f"Queue empty for user {user_id}")
            return None
    
    async def populate(self, user_id: str, completion_event: Optional[asyncio.Event] = None):
        """Optimized populate task with dynamic loading"""
        try:
            while True:
                try:
                    if await self.check_stop_flag(user_id):
                        logger.info(f"Stopping populate task for user {user_id}")
                        break
                    
                    user_queue = await self.get_user_queue(user_id)
                    current_size = user_queue.qsize()
                    
                    # Only load when below threshold
                    if current_size <= self.prefetch_threshold:
                        logger.debug(f"Queue below threshold for {user_id}, loading more")
                        await self.load_movies_into_queue(user_id)
                    
                    # Adaptive sleep based on queue size
                    if current_size >= self.queue_size - 2:
                        await asyncio.sleep(5.0)  # Queue nearly full, sleep longer
                    elif current_size >= self.prefetch_threshold:
                        await asyncio.sleep(2.0)  # Queue healthy, moderate sleep
                    else:
                        await asyncio.sleep(0.5)  # Queue low, check frequently
                    
                except asyncio.CancelledError:
                    logger.info(f"Populate task cancelled for user {user_id}")
                    break
                except Exception as e:
                    logger.error(f"Error in populate task for {user_id}: {e}", exc_info=True)
                    await asyncio.sleep(5.0)  # Back off on error
        
        finally:
            if completion_event:
                completion_event.set()
    
    async def get_metrics(self) -> Dict[str, Any]:
        """Get performance metrics"""
        return {
            **self.metrics,
            'active_users': len(self.user_queues),
            'total_queued': sum(
                info['queue'].qsize() 
                for info in self.user_queues.values()
            )
        }
    
    async def cleanup(self):
        """Cleanup resources"""
        if self._http_client:
            await self._http_client.aclose()
        if self._redis_client:
            await self._redis_client.close()
    
    # Keep existing methods for compatibility
    async def set_stop_flag(self, user_id: str, stop: bool = True):
        self.stop_flags[user_id] = stop
    
    async def check_stop_flag(self, user_id: str) -> bool:
        return self.stop_flags.get(user_id, False)
    
    async def mark_movie_seen(self, user_id: str, tconst: str):
        user_info = self.user_queues.get(user_id, {})
        seen = user_info.setdefault("seen_tconsts", set())
        seen.add(tconst)
    
    async def fetch_and_enqueue_movie(self, tconst: str, user_id: str):
        """Compatibility method - enqueue a single movie"""
        try:
            # Get user info
            user_info = self.user_queues.get(user_id, {})
            if not user_info:
                await self.get_user_queue(user_id)
                user_info = self.user_queues[user_id]
            
            seen = user_info.get("seen_tconsts", set())
            queued = user_info.setdefault("queued_tconsts", set())
            
            # Skip if already seen or queued
            if tconst in seen or tconst in queued:
                logger.debug(f"Movie {tconst} already seen/queued for user {user_id}")
                return
            
            user_queue = await self.get_user_queue(user_id)
            
            if not user_queue.full():
                # Add to queue with metadata only
                metadata = MovieMetadata(tconst=tconst)
                await user_queue.put(metadata)
                queued.add(tconst)
                self.movie_enqueue_count += 1
                logger.debug(f"Enqueued movie {tconst} for user {user_id}")
            else:
                logger.warning(f"Queue full for user {user_id}, cannot enqueue {tconst}")
                
        except Exception as e:
            logger.error(f"Error enqueueing movie {tconst} for user {user_id}: {e}")
    
    async def set_criteria(self, user_id: str, criteria: Dict[str, Any]):
        """Set filtering criteria for a user"""
        user_info = self.user_queues.get(user_id, {})
        if not user_info:
            await self.get_user_queue(user_id)
            user_info = self.user_queues[user_id]
        user_info["criteria"] = criteria
        logger.info(f"Updated criteria for user {user_id}")
    
    async def empty_queue(self, user_id: str):
        """Empty a user's queue"""
        try:
            user_info = self.user_queues.get(user_id, {})
            if user_info:
                queue = user_info["queue"]
                while not queue.empty():
                    await queue.get()
                # Also clear the queued set
                user_info["queued_tconsts"] = set()
                logger.info(f"Emptied queue for user {user_id}")
        except Exception as e:
            logger.error(f"Error emptying queue for user {user_id}: {e}")
    
    async def start_populate_task(self, user_id: str):
        """Start the populate task for a user"""
        user_info = self.user_queues.get(user_id, {})
        if user_info:
            task = user_info.get("populate_task")
            if not task or task.done():
                user_info["populate_task"] = asyncio.create_task(self.populate(user_id))
                logger.info(f"Started populate task for user {user_id}")
    
    async def stop_populate_task(self, user_id: str):
        """Stop the populate task for a user"""
        await self.set_stop_flag(user_id, True)
        user_info = self.user_queues.get(user_id, {})
        if user_info and user_info.get("populate_task"):
            user_info["populate_task"].cancel()
            try:
                await user_info["populate_task"]
            except asyncio.CancelledError:
                pass
            logger.info(f"Stopped populate task for user {user_id}")
    
    async def update_criteria_and_reset(self, user_id: str, new_criteria: Dict[str, Any]):
        """Update criteria and restart queue population"""
        await self.set_criteria(user_id, new_criteria)
        await self.empty_queue(user_id)
        await self.start_populate_task(user_id)
    
    async def add_user(self, user_id: str, criteria: Dict[str, Any]):
        """Add a new user with criteria and start population"""
        if user_id not in self.user_queues:
            await self.get_user_queue(user_id)
            await self.set_criteria(user_id, criteria)
            await self.start_populate_task(user_id)
            logger.info(f"Added user {user_id} with criteria")
    
    def is_task_running(self, user_id: Optional[str] = None) -> bool:
        """Check if populate task is running"""
        if user_id:
            user_info = self.user_queues.get(user_id, {})
            task = user_info.get("populate_task")
            return task is not None and not task.done()
        
        # Check all users if no user_id specified
        for info in self.user_queues.values():
            task = info.get("populate_task")
            if task and not task.done():
                return True
        return False
    
    async def reset_seen_movies(self, user_id: str):
        """Reset seen movies for a user"""
        if user_id in self.user_queues:
            self.user_queues[user_id]["seen_tconsts"] = set()
            logger.info(f"Reset seen movies for user {user_id}")


# For backward compatibility, alias the optimized version
MovieQueue = OptimizedMovieQueue