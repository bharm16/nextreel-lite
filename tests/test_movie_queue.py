import asyncio
from scripts.movie_queue import MovieQueue


class DummyFetcher:
    async def fetch_random_movies15(self, criteria):
        return []


def test_get_user_queue_and_stop_flag():
    async def run_test():
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
        mq = MovieQueue(db_config=None, queue=asyncio.Queue(), movie_fetcher=DummyFetcher())
        assert mq.is_task_running('u1') is False
        task = asyncio.create_task(asyncio.sleep(0))
        mq.user_queues['u1'] = {'populate_task': task}
        assert mq.is_task_running('u1') is True
        await task
        assert mq.is_task_running('u1') is False

    asyncio.run(run_test())
