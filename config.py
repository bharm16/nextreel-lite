import os
import pymysql
import tmdbsimple
# Removed the conflicting import of Config from flask
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
    # Database configurations
    STACKHERO_DB_CONFIG = {
        'host': os.getenv('STACKHERO_DB_HOST'),
        'user': os.getenv('STACKHERO_DB_USER'),
        'password': os.getenv('STACKHERO_DB_PASSWORD'),
        'database': os.getenv('STACKHERO_DB_NAME'),
        'port': int(os.getenv('STACKHERO_DB_PORT')) if os.getenv('STACKHERO_DB_PORT') else 3306
    }

    # Check if running on Heroku by looking for a unique Heroku environment variable
    if os.getenv('DYNO'):
        # If running on Heroku, set the PROJECT_ROOT to '/app'
        PROJECT_ROOT = '/app'
    else:
        # If not running on Heroku, use the local development path or another environment variable
        PROJECT_ROOT = os.getenv('PROJECT_ROOT', '/Users/bryceharmon/Desktop/nextreel-lite')

    # SSL_CERT_PATH can be set through an environment variable or determined based on the PROJECT_ROOT
    SSL_CERT_PATH = os.getenv('SSL_CERT_PATH') or os.path.join(PROJECT_ROOT, 'isrgroot.pem')

    SECRET_KEY = os.getenv('FLASK_SECRET_KEY')
    TMDB_API_KEY = os.getenv('TMDB_API_KEY')


def create_connection():
    ssl_config = None

    # Check if the SSL certificate file exists at the specified path
    if os.path.isfile(Config.SSL_CERT_PATH):
        ssl_config = {'ca': Config.SSL_CERT_PATH}
    else:
        print(f"SSL certificate file not found at {Config.SSL_CERT_PATH}")
        return None

    print(f"Attempting to connect to the database with SSL configuration: {ssl_config}")

    try:
        print("Establishing database connection...")
        connection = pymysql.connect(
            host=Config.STACKHERO_DB_CONFIG['host'],
            user=Config.STACKHERO_DB_CONFIG['user'],
            password=Config.STACKHERO_DB_CONFIG['password'],
            database=Config.STACKHERO_DB_CONFIG['database'],
            port=Config.STACKHERO_DB_CONFIG['port'],
            cursorclass=DictCursor,
            ssl=ssl_config
        )
        print("Database connection established successfully.")
        return connection
    except pymysql.MySQLError as err:
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
