import asyncio
import os
import random

import httpx
import tmdbsimple as tmdb

api_key = os.getenv('TMDB_API_KEY')
tmdb.API_KEY = api_key

# Replace with your actual TMDb API key
TMDB_API_KEY = '1ce9398920594a5521f0d53e9b33c52f'
TMDB_IMAGE_BASE_URL = "https://image.tmdb.org/t/p/"

# Replace with your actual TMDb API key
TMDB_API_BASE_URL = "https://api.themoviedb.org/3"

# Use os.path.dirname to go up one level from the current script's directory
parent_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Now change the working directory to the parent directory
os.chdir(parent_dir)




import httpx
import random

class TMDbHelper:
    def __init__(self, api_key):
        self.api_key = api_key
        self.base_url = "https://api.themoviedb.org/3"
        self.image_base_url = "https://image.tmdb.org/t/p/"

    async def _get(self, endpoint, params={}):
        async with httpx.AsyncClient() as client:
            url = f"{self.base_url}/{endpoint}"
            params['api_key'] = self.api_key
            response = await client.get(url, params=params)
            response.raise_for_status()
            return response.json()

    async def get_cast_info_by_tmdb_id(self, tmdb_id):
        data = await self._get(f"movie/{tmdb_id}/credits")
        cast_info = []
        for cast_member in data.get('cast', [])[:10]:
            profile_path = cast_member.get('profile_path')
            image_url = f"{self.image_base_url}w185{profile_path}" if profile_path else None
            character_name = cast_member.get('character', 'N/A')
            cast_info.append({
                'name': cast_member['name'],
                'image_url': image_url,
                'character': character_name
            })
        return cast_info

    async def get_video_url_by_tmdb_id(self, tmdb_id):
        data = await self._get(f"movie/{tmdb_id}/videos")
        for video in data.get('results', []):
            if video['site'] == 'YouTube' and video['type'] == 'Trailer':
                return f"https://www.youtube.com/watch?v={video['key']}"
        return None

    async def get_images_by_tmdb_id(self, tmdb_id):
        data = await self._get(f"movie/{tmdb_id}/images")
        return {
            'posters': [self.image_base_url + 'original' + img['file_path'] for img in data.get('posters', []) if
                        'file_path' in img],
            'backdrops': [self.image_base_url + 'original' + img['file_path'] for img in data.get('backdrops', []) if
                          'file_path' in img]
        }

    async def get_videos_by_tmdb_id(self, tmdb_id):
        return await self._get(f"movie/{tmdb_id}/videos")

    async def get_credits_by_tmdb_id(self, tmdb_id):
        return await self._get(f"movie/{tmdb_id}/credits")

    async def get_tmdb_id_by_tconst(self, tconst):
        data = await self._get("find/" + tconst, {'external_source': 'imdb_id'})
        return data['movie_results'][0]['id'] if data['movie_results'] else None

    async def get_movie_info_by_tmdb_id(self, tmdb_id):
        return await self._get(f"movie/{tmdb_id}")

    def get_full_image_url(self, profile_path, size='original'):
        return f"{self.image_base_url}{size}{profile_path}"

    async def get_backdrop_image_for_home(self, tmdb_id):
        image_data = await self.get_images_by_tmdb_id(tmdb_id)
        backdrops = image_data['backdrops']
        if backdrops:
            return self.get_full_image_url(backdrops[0])
        return None

    async def get_all_backdrop_images(self, tmdb_id):
        image_data = await self.get_images_by_tmdb_id(tmdb_id)
        backdrops = image_data['backdrops']
        return [self.get_full_image_url(backdrop) for backdrop in backdrops]

    async def get_backdrop_for_movie(self, tmdb_id):
        all_backdrop_urls = await self.get_all_backdrop_images(tmdb_id)
        return random.choice(all_backdrop_urls) if all_backdrop_urls else None






# Class for managing TMDb movie information
class TmdbMovieInfo:
    def __init__(self, api_key):
        self.api_key = api_key
        tmdb.API_KEY = self.api_key


async def main(api_key, tconst):
    tmdb_helper = TMDbHelper(api_key)

    tmdb_id = await tmdb_helper.get_tmdb_id_by_tconst(tconst)
    if tmdb_id:
        movie_info = await tmdb_helper.get_movie_info_by_tmdb_id(tmdb_id)
        print("Movie Information from TMDb:", movie_info)

        cast_info = await tmdb_helper.get_cast_info_by_tmdb_id(tmdb_id)
        print("Cast Information:")
        for cast_member in cast_info:
            print(f"{cast_member['name']} as {cast_member['character']}")
            if cast_member.get('image_url'):
                print(f"Image URL: {cast_member['image_url']}")
            else:
                print("Image not available")

        all_backdrops = await tmdb_helper.get_all_backdrop_images(tmdb_id)
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
