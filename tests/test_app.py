import pytest
from quart import Quart
from app import create_app

@pytest.fixture
async def app() -> Quart:
    # Create an instance of the Quart application
    app = create_app()
    async with app.app_context():
        # Yield the app instance in an async context
        yield app

@pytest.fixture
async def client(app):
    # Return the test client directly
    return app.test_client()

@pytest.mark.asyncio
async def test_home(client):
    # Directly use the client fixture to make a request
    response = await client.get("/")
    assert response.status_code == 200
    # Additional assertions as needed
