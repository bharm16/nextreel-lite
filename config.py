import os

from flask.cli import load_dotenv

# Load environment variables from .env file
load_dotenv()


class Config:
    SECRET_KEY = os.getenv('FLASK_SECRET_KEY')
    TMDB_API_KEY = os.getenv('TMDB_API_KEY')

    # Database configurations
    USER_DB_CONFIG = {
        'host': os.getenv('DB_HOST'),
        'user': os.getenv('DB_USER'),
        'password': os.getenv('DB_PASSWORD'),
        'database': os.getenv('DB_NAME')
    }

    STACKHERO_DB_CONFIG = {
        'host': os.getenv('STACKHERO_DB_HOST'),
        'user': os.getenv('STACKHERO_DB_USER'),
        'password': os.getenv('STACKHERO_DB_PASSWORD'),
        'database': os.getenv('STACKHERO_DB_NAME'),
        'port': int(os.getenv('STACKHERO_DB_PORT'))
    }
