import asyncio
from app import create_app


def test_home():
    """Ensure the home route returns HTTP 200."""

    async def run_test():
        app = create_app()
        async with app.app_context():
            client = app.test_client()
            response = await client.get("/")
            assert response.status_code == 200

    asyncio.run(run_test())
