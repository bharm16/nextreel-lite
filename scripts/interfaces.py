from typing import Protocol, List, Dict, Any


class MovieFetcher(Protocol):
    """Abstraction for objects that can fetch movies based on criteria."""

    async def fetch_random_movies(self, criteria: Dict[str, Any], limit: int) -> List[Dict[str, Any]]:
        ...
