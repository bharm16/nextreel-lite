import pytest
from unittest.mock import AsyncMock, patch

from movie_service import MovieManager


@pytest.mark.asyncio
async def test_start():
    # Create a mock for the set_default_backdrop method
    set_default_backdrop_mock = AsyncMock()

    # Patch the set_default_backdrop method in the MovieManager class
    with patch.object(MovieManager, 'set_default_backdrop', set_default_backdrop_mock):
        movie_manager = MovieManager(db_config=None)  # Replace None with actual db_config if needed
        await movie_manager.start()

        # Assert that set_default_backdrop was called once
        set_default_backdrop_mock.assert_called_once()

        # Bonus: If you want to check the logging, you would need to patch 'logging.info' and check calls


@pytest.mark.asyncio
async def test_add_user():
    # Mock for movie_queue_manager's add_user method
    add_user_mock = AsyncMock()

    # Instance of MovieManager with a mocked movie_queue_manager
    movie_manager = MovieManager(db_config=None)  # Replace None with actual db_config if needed
    movie_manager.movie_queue_manager = AsyncMock(add_user=add_user_mock)

    user_id = "test_user"
    criteria = {"genre": "comedy"}

    # Call the add_user method
    await movie_manager.add_user(user_id, criteria)

    # Assert that movie_queue_manager's add_user was called once with correct arguments
    add_user_mock.assert_called_once_with(user_id, criteria)

    # Bonus: To test logging, you would need to patch 'logging.info' and check its calls


@pytest.mark.asyncio
async def test_home():
    # Mocks for dependencies
    is_task_running_mock = AsyncMock(return_value=False)
    populate_mock = AsyncMock()
    render_template_mock = AsyncMock(return_value='rendered_template')

    # Instance of MovieManager with mocked dependencies
    movie_manager = MovieManager(db_config=None)  # Replace None with actual db_config if needed
    movie_manager.movie_queue_manager = AsyncMock(
        is_task_running=is_task_running_mock,
        populate=populate_mock
    )

    with patch('movie_service.render_template', render_template_mock):
        user_id = "test_user"
        result = await movie_manager.home(user_id)

        # Asserts
        is_task_running_mock.assert_called_once()
        populate_mock.assert_called_once_with(user_id)
        render_template_mock.assert_called_once_with('home.html',
                                                     default_backdrop_url=movie_manager.default_backdrop_url)
        assert result == 'rendered_template'

    @pytest.mark.asyncio
    async def test_set_default_backdrop():
        # Mock for tmdb_helper's get_images_by_tmdb_id method
        get_images_by_tmdb_id_mock = AsyncMock(return_value={'backdrops': ['backdrop_url']})

        # Instance of MovieManager with a mocked tmdb_helper
        movie_manager = MovieManager(db_config=None)  # Replace None with actual db_config if needed
        movie_manager.tmdb_helper = AsyncMock(get_images_by_tmdb_id=get_images_by_tmdb_id_mock)

        await movie_manager.set_default_backdrop()

        # Assert that default_backdrop_url is set correctly
        get_images_by_tmdb_id_mock.assert_called_once_with(movie_manager.default_movie_tmdb_id)
        assert movie_manager.default_backdrop_url == movie_manager.tmdb_helper.get_full_image_url('backdrop_url')

