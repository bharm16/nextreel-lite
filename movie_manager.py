import asyncio
import logging
import httpx
import random
from quart import render_template
from config import Config
from scripts.movie_queue import MovieQueue
from scripts.set_filters_for_nextreel_backend import ImdbRandomMovieFetcher, extract_movie_filter_criteria
from scripts.tmdb_data import get_all_backdrop_images

# Configure logging for better debugging
logging.basicConfig(level=logging.INFO)


class MovieManager:
    def __init__(self, db_config):
        logging.info("Initializing MovieManager")
        self.movie_fetcher = ImdbRandomMovieFetcher(db_config)
        self.criteria = {}
        self.movie_queue = asyncio.Queue(maxsize=15)
        self.movie_queue_manager = MovieQueue(db_config, self.movie_queue)
        self.future_movies_stack = []
        self.previous_movies_stack = []
        self.current_displayed_movie = None
        self.default_movie_tmdb_id = 62
        self.default_backdrop_url = None

    async def start_population_task(self):
        logging.info("Starting population task")
        if not self.movie_queue_manager.is_task_running():
            self.movie_queue_manager.populate_task = asyncio.create_task(self.movie_queue_manager.populate())

    async def start(self):
        logging.info("Starting MovieManager")
        await self.start_population_task()
        await self.set_default_backdrop_url()

    async def set_default_backdrop_url(self):
        logging.info("Setting default backdrop URL")
        async with httpx.AsyncClient() as client:
            all_backdrops = await get_all_backdrop_images(self.default_movie_tmdb_id, client)
            self.default_backdrop_url = self.select_one_backdrop(all_backdrops)

    async def fetch_and_render_movie(self, template_name='movie.html'):
        logging.info("Fetching and rendering movie")
        async with httpx.AsyncClient() as client:
            while True:
                if self.movie_queue.empty():
                    logging.info("Movie queue is empty")
                    return None
                self.current_displayed_movie = await self.movie_queue.get()
                tmdb_id = self.current_displayed_movie.get('tmdb_id')
                all_backdrops = await get_all_backdrop_images(tmdb_id, client)

                selected_backdrop_url = self.select_one_backdrop(all_backdrops)
                if selected_backdrop_url:
                    self.current_displayed_movie['backdrop_url'] = selected_backdrop_url
                    return await render_template(template_name, movie=self.current_displayed_movie,
                                                 previous_count=len(self.previous_movies_stack))
                logging.info("Movie skipped due to missing backdrop image")

    def select_one_backdrop(self, backdrops):
        if not backdrops:
            return None
        return random.choice(backdrops)

    async def next_movie(self):
        logging.info("Fetching next movie")
        if self.current_displayed_movie:
            self.previous_movies_stack.append(self.current_displayed_movie)
        if self.future_movies_stack:
            self.current_displayed_movie = self.future_movies_stack.pop()
        elif not self.movie_queue.empty():
            self.current_displayed_movie = await self.movie_queue.get()
        else:
            self.current_displayed_movie = None

        return await self.fetch_and_render_movie()

    async def previous_movie(self):
        logging.info("Fetching previous movie")
        if self.current_displayed_movie:
            self.future_movies_stack.append(self.current_displayed_movie)
        if self.previous_movies_stack:
            self.current_displayed_movie = self.previous_movies_stack.pop()
        else:
            self.current_displayed_movie = None

        return await self.fetch_and_render_movie()

    async def set_filters(self):
        logging.info("Setting filters")
        start_time = asyncio.get_event_loop().time()
        await self.movie_queue_manager.stop_populate_task()
        await self.movie_queue_manager.empty_queue()
        self.current_displayed_movie = None
        logging.info(f"Filters set in {asyncio.get_event_loop().time() - start_time} seconds")
        return await render_template('set_filters.html')

    async def home(self):
        logging.info("Accessing home")
        return await render_template('home.html', default_backdrop_url=self.default_backdrop_url)

    async def filtered_movie(self, form_data):
        logging.info("Filtering movie")
        new_criteria = extract_movie_filter_criteria(form_data)
        self.criteria = new_criteria
        await self.movie_queue_manager.stop_populate_task()
        await self.movie_queue_manager.empty_queue()
        await self.movie_queue_manager.set_criteria(self.criteria)
        self.movie_queue_manager.populate_task = asyncio.create_task(self.movie_queue_manager.populate())
        logging.info("Criteria updated, repopulating movie queue")
        await asyncio.sleep(20)  # Giving time for queue to populate
        return await self.fetch_and_render_movie()


# Main function for testing...
async def main():
    dbconfig = Config.STACKHERO_DB_CONFIG
    movie_manager = MovieManager(dbconfig)
    await movie_manager.start()
    await asyncio.sleep(10)  # Wait for queue to populate
    rendered_movie = await movie_manager.fetch_and_render_movie()
    next_movie_render = await movie_manager.next_movie()
    prev_movie_render = await movie_manager.previous_movie()


if __name__ == "__main__":
    asyncio.run(main())
