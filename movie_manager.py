import asyncio
import logging

import httpx
# Update imports for async handling
from quart import render_template

from config import Config
from scripts.movie_queue import MovieQueue
from scripts.set_filters_for_nextreel_backend import ImdbRandomMovieFetcher, extract_movie_filter_criteria


# This function should be async because it performs an HTTP request
async def get_backdrop_image_for_home(tmdb_id, client):
    # Perform your HTTP request here to get the backdrop image using the provided client
    # I'm assuming you have an existing function that fetches the backdrop image data.
    # Here's a simplified example:
    url = f"https://api.themoviedb.org/3/movie/{tmdb_id}/images"
    params = {'api_key': '1ce9398920594a5521f0d53e9b33c52f'}  # Replace with your actual API key
    response = await client.get(url, params=params)
    response.raise_for_status()
    data = response.json()
    if data['backdrops']:
        return data['backdrops'][0]['file_path']  # Return the path of the first backdrop image
    return None


class MovieManager:
    def __init__(self, db_config):
        self.movie_fetcher = ImdbRandomMovieFetcher(db_config)
        self.criteria = {}
        self.movie_queue = asyncio.Queue(maxsize=15)  # Use asyncio.Queue for async compatibility
        self.movie_queue_manager = MovieQueue(db_config, self.movie_queue)
        self.future_movies_stack = []
        self.previous_movies_stack = []
        self.current_displayed_movie = None
        self.default_movie_tmdb_id = 62
        self.default_backdrop_url = None  # Initialize with None
        # Initiate the process of setting the default backdrop URL asynchronously

    async def start(self):
        # Now it's safe to start asynchronous tasks because this method should be called from an async context
        await self.movie_queue_manager.populate()  # Start populating the queue
        await self.set_default_backdrop_url()  # Set the default backdrop URL

    async def set_default_backdrop_url(self):
        # Create an instance of the HTTP client
        async with httpx.AsyncClient() as client:
            # Now pass the client to the get_backdrop_image_for_home function
            self.default_backdrop_url = await get_backdrop_image_for_home(self.default_movie_tmdb_id, client)

    async def fetch_and_render_movie(self, template_name='movie.html'):
        while True:
            logging.info(f"Checking queue size: {self.movie_queue.qsize()}")
            if self.movie_queue.empty():
                print("Queue is empty, and no current movie is displayed with a valid backdrop image.")
                return None

            self.current_displayed_movie = await self.movie_queue.get()
            print(f"Fetched new movie: {self.current_displayed_movie['title']}")

            if 'backdrop_path' in self.current_displayed_movie and self.current_displayed_movie['backdrop_path']:
                return await render_template(template_name,
                                             movie=self.current_displayed_movie,
                                             previous_count=len(self.previous_movies_stack))
            else:
                print(f"Skipping movie '{self.current_displayed_movie['title']}' due to missing backdrop image.")
                self.current_displayed_movie = None


    async def next_movie(self):
        if self.current_displayed_movie:
            self.previous_movies_stack.append(self.current_displayed_movie)
            logging.info(f"Moved current movie to previous stack: {self.current_displayed_movie['title']}")

        if self.future_movies_stack:
            self.current_displayed_movie = self.future_movies_stack.pop()
            logging.info(f"Retrieved next movie from future stack: {self.current_displayed_movie['title']}")
        elif not self.movie_queue.empty():
            self.current_displayed_movie = await self.movie_queue.get()  # Use await here
            logging.info(f"Fetched next movie from queue: {self.current_displayed_movie['title']}")
        else:
            self.current_displayed_movie = None
            logging.info("No movies in future stack and queue is empty.")

        return await self.fetch_and_render_movie()

    async def previous_movie(self):
        if self.current_displayed_movie:
            self.future_movies_stack.append(self.current_displayed_movie)
            logging.info(f"Moved current movie to future stack: {self.current_displayed_movie['title']}")

        if self.previous_movies_stack:
            self.current_displayed_movie = self.previous_movies_stack.pop()
            logging.info(f"Retrieved previous movie: {self.current_displayed_movie['title']}")
        else:
            logging.info("No previous movies to retrieve.")

        return await self.fetch_and_render_movie()

    async def update_criteria(self, new_criteria):
        self.criteria = new_criteria
        await self.movie_queue_manager.update_criteria_and_reset(self.criteria)  # Use await here
        logging.info("Criteria updated: %s", self.criteria)

    async def set_filters(self):
        start_time = asyncio.get_event_loop().time()
        logging.info("Entering setFilters")

        await self.movie_queue_manager.stop_populate_task()  # Use await here
        logging.info(f"Stopping populate task took {asyncio.get_event_loop().time() - start_time} seconds")

        await self.movie_queue_manager.empty_queue()  # Use await here
        logging.info(f"Emptying queue took {asyncio.get_event_loop().time() - start_time} seconds")

        self.current_displayed_movie = None
        logging.info("Current displayed movie has been reset due to filter change.")

        logging.info(f"Total time taken for setFilters: {asyncio.get_event_loop().time() - start_time} seconds")
        return await render_template('set_filters.html')  # If render_template is async compatible

    async def home(self):
        return await render_template('home.html',
                                     default_backdrop_url=self.default_backdrop_url)  # If render_template is async compatible

    async def filtered_movie(self, form_data):
        # Extract new filter criteria from the form
        new_criteria = extract_movie_filter_criteria(form_data)

        # Update the instance criteria with the new filters
        self.criteria = new_criteria

        # Update the existing movie queue manager with the new filter criteria
        await self.movie_queue_manager.update_criteria_and_reset(self.criteria)  # Use await here

        # For debugging purposes, log out the new criteria
        logging.info("Extracted criteria: %s", new_criteria)

        # We give the queue a few seconds to populate with movies that match the new criteria
        await asyncio.sleep(5)  # Non-blocking sleep

        # Return the rendered movie
        return await self.fetch_and_render_movie()


# ... Your existing code ...

# The main function for testing
async def main():
    dbconfig = Config.STACKHERO_DB_CONFIG
    movie_manager = MovieManager(dbconfig)
    await movie_manager.start()

    await asyncio.sleep(10)  # Wait for queue to populate
    logging.info(f"Queue size after waiting: {movie_manager.movie_queue.qsize()}")

    rendered_movie = await movie_manager.fetch_and_render_movie()
    logging.info(f"Rendered movie: {rendered_movie}")

    # Test updating criteria
    new_criteria = {
        'min_year': 2000,
        'max_year': 2023,
        'min_rating': 7.0,
        'max_rating': 10,
        'genres': ['Comedy', 'Romance']
    }

    # Test getting the next movie
    next_movie_render = await movie_manager.next_movie()
    logging.info(f"Next movie rendered: {next_movie_render}")

    # Test getting the previous movie
    prev_movie_render = await movie_manager.previous_movie()
    logging.info(f"Previous movie rendered: {prev_movie_render}")


# Run the main function
if __name__ == "__main__":
    asyncio.run(main())
