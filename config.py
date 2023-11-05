import os
import pymysql
import tmdbsimple
from pymysql.cursors import DictCursor
from flask.cli import load_dotenv

# Load environment variables from .env file
load_dotenv()

# Fetch the API key from environment variable
api_key = os.getenv('TMDB_API_KEY')
if not api_key:
    raise ValueError("No TMDB_API_KEY found in environment variables")

# Initialize the tmdb API with the key fetched from the environment
tmdbsimple.API_KEY = api_key


class Config:
    SECRET_KEY = os.getenv('FLASK_SECRET_KEY')
    TMDB_API_KEY = os.getenv('TMDB_API_KEY')

    # Database configurations
    STACKHERO_DB_CONFIG = {
        'host': os.getenv('STACKHERO_DB_HOST'),
        'user': os.getenv('STACKHERO_DB_USER'),
        'password': os.getenv('STACKHERO_DB_PASSWORD'),
        'database': os.getenv('STACKHERO_DB_NAME'),
        'port': int(os.getenv('STACKHERO_DB_PORT')) if os.getenv('STACKHERO_DB_PORT') else 3306
    }


# SSL certificate path
SSL_CERT_PATH = "/Users/bryceharmon/Desktop/isrgroot.pem"


def create_connection():
    # Define SSL configuration
    ssl_config = {'ca': SSL_CERT_PATH}

    # Print the SSL configuration for debugging
    print(f"Attempting to connect to the database with SSL configuration: {ssl_config}")

    try:
        # Print before attempting to connect
        print("Establishing database connection...")

        # Establish a secure connection using the SSL configuration
        connection = pymysql.connect(
            host=Config.STACKHERO_DB_CONFIG['host'],
            user=Config.STACKHERO_DB_CONFIG['user'],
            password=Config.STACKHERO_DB_CONFIG['password'],
            database=Config.STACKHERO_DB_CONFIG['database'],
            port=Config.STACKHERO_DB_CONFIG['port'],
            cursorclass=DictCursor,
            ssl=ssl_config
        )

        # Print after successful connection
        print("Database connection established successfully.")
        return connection

    except pymysql.MySQLError as err:
        # Print the error if connection fails
        print(f"Error: '{err}'")
        return None


# Example usage
print("Starting the database connection process.")
connection = create_connection()

if connection:
    # Print that the database operations can proceed
    print("Ready to perform database operations.")
else:
    # Print if the connection was not successful
    print("Database connection could not be established.")
