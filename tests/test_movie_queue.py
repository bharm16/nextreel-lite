import asyncio
from unittest.mock import patch

from scripts.movie_queue import MovieQueue


class DummyFetcher:
    async def fetch_random_movies15(self, criteria):
        return []


def test_get_user_queue_and_stop_flag():
    async def run_test():
        MovieQueue._instance = None
        queue = asyncio.Queue()
        mq = MovieQueue(db_config=None, queue=queue, movie_fetcher=DummyFetcher())
        user_queue = await mq.get_user_queue('u1')
        assert 'u1' in mq.user_queues
        assert isinstance(user_queue, asyncio.Queue)
        await mq.set_stop_flag('u1', True)
        assert await mq.check_stop_flag('u1') is True

    asyncio.run(run_test())


def test_is_task_running():
    async def run_test():
        MovieQueue._instance = None
        mq = MovieQueue(db_config=None, queue=asyncio.Queue(), movie_fetcher=DummyFetcher())
        assert mq.is_task_running('u1') is False
        task = asyncio.create_task(asyncio.sleep(0))
        mq.user_queues['u1'] = {'populate_task': task}
        assert mq.is_task_running('u1') is True
        await task
        assert mq.is_task_running('u1') is False

    asyncio.run(run_test())


def test_enqueue_movie_deduplication():
    async def run_test():
        MovieQueue._instance = None
        mq = MovieQueue(db_config=None, queue=asyncio.Queue(), movie_fetcher=DummyFetcher())

        class DummyMovie:
            def __init__(self, tconst, db_config):
                self.tconst = tconst

            async def get_movie_data(self):
                return {"imdb_id": self.tconst, "title": self.tconst}

        with patch('scripts.movie_queue.Movie', DummyMovie):
            await mq.get_user_queue('u1')
            await mq.fetch_and_enqueue_movie('tt1', 'u1')
            await mq.fetch_and_enqueue_movie('tt1', 'u1')
            assert mq.user_queues['u1']["queue"].qsize() == 1

            await mq.mark_movie_seen('u1', 'tt1')
            await mq.fetch_and_enqueue_movie('tt1', 'u1')
            assert mq.user_queues['u1']["queue"].qsize() == 1

    asyncio.run(run_test())
