import asyncio
import os

import httpx
import tmdbsimple as tmdb


api_key = os.getenv('TMDB_API_KEY')
tmdb.API_KEY = api_key

# Use os.path.dirname to go up one level from the current script's directory
parent_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Now change the working directory to the parent directory
os.chdir(parent_dir)

# Finally, print the new working directory to confirm the change
# print(f"Current working directory after change: {os.getcwd()}")


# Async function to fetch TMDb ID using IMDb tconst
async def get_tmdb_id_by_tconst(tconst, client):
    url = f"https://api.themoviedb.org/3/find/{tconst}"
    params = {
        'api_key': api_key,
        'external_source': 'imdb_id'
    }
    response = await client.get(url, params=params)
    response.raise_for_status()  # This will raise an exception for HTTP error responses
    data = response.json()
    tmdb_id = data['movie_results'][0]['id'] if data['movie_results'] else None
    return tmdb_id


# Async function to fetch movie information by TMDb ID
async def get_movie_info_by_tmdb_id(tmdb_id, client):
    url = f"https://api.themoviedb.org/3/movie/{tmdb_id}"
    params = {'api_key': api_key}
    response = await client.get(url, params=params)
    response.raise_for_status()
    return response.json()


# Function to fetch cast information by TMDb ID
def get_cast_info_by_tmdb_id(tmdb_id):
    movie = tmdb.Movies(tmdb_id)
    response = movie.credits()
    return response.get('cast', [])


# Function to get full image URL
def get_full_image_url(profile_path, size='original'):
    base_url = "https://image.tmdb.org/t/p/"
    return f"{base_url}{size}{profile_path}"


async def get_backdrop_image_for_home(tmdb_id, client):
    """Asynchronously gets a backdrop image for the homepage."""
    if tmdb_id:
        image_data = await fetch_images_from_tmdb(tmdb_id, client)
        backdrops = image_data.get('backdrops', [])
        if backdrops:
            backdrop_url = await get_full_image_url(backdrops[0]['file_path'])  # Using the first backdrop image
            return backdrop_url
    return None


async def get_all_backdrop_images(tmdb_id, client):
    """Asynchronously gets all backdrop images."""
    if tmdb_id:
        image_data = await fetch_images_from_tmdb(tmdb_id, client)
        backdrops = image_data.get('backdrops', [])
        all_backdrop_urls = []
        for backdrop in backdrops:
            backdrop_url = await get_full_image_url(backdrop['file_path'])
            all_backdrop_urls.append(backdrop_url)
        return all_backdrop_urls
    return None


# Class for managing TMDb movie information
class TmdbMovieInfo:
    def __init__(self, api_key):
        self.api_key = api_key
        tmdb.API_KEY = self.api_key


async def main(api_key, tconst):
    async with httpx.AsyncClient() as client:
        tmdb_info = TmdbMovieInfo(api_key)
        tmdb_id = await tmdb_info.get_tmdb_id_by_tconst(tconst, client)

        if tmdb_id:
            movie_info = await tmdb_info.get_movie_info_by_tmdb_id(tmdb_id, client)
            print("Movie Information from TMDb:", movie_info)

            cast_info = await tmdb_info.get_cast_info_by_tmdb_id(tmdb_id, client)
            print("Cast Information:")
            for cast_member in cast_info:
                print(f"{cast_member['name']} as {cast_member['character']}")
                profile_path = cast_member.get('profile_path')
                if profile_path:
                    image_url = await tmdb_info.get_full_image_url(profile_path)
                    print(f"Image URL: {image_url}")
                else:
                    print("Image not available")

            all_backdrops = await tmdb_info.get_all_backdrop_images(tmdb_id, client)
            if all_backdrops:
                print("All backdrop images:")
                for backdrop in all_backdrops:
                    print(backdrop)
            else:
                print("No backdrop images found.")
        else:
            print("TMDb ID not found.")


if __name__ == "__main__":
    api_key = '1ce9398920594a5521f0d53e9b33c52f'  # Replace with your actual TMDb API key
    tconst = 'tt0111161'  # Replace with the IMDb tconst you have
    asyncio.run(main(api_key, tconst))