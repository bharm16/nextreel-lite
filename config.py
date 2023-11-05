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
    # SSL certificate path from an environment variable or a relative path
    SSL_CERT_PATH = os.getenv('SSL_CERT_PATH', 'certs/isrgroot.pem')
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
    ssl_config = None

    # Only add SSL configuration if SSL_CERT_PATH is provided
    if Config.SSL_CERT_PATH:
        # Assuming the current working directory is the root of your project
        # Update the path to the certificate if your project structure is different
        ssl_cert_full_path = os.path.join(os.getcwd(), Config.SSL_CERT_PATH)

        if not os.path.isfile(ssl_cert_full_path):
            print(f"SSL certificate file not found at {ssl_cert_full_path}")
            return None
        ssl_config = {'ca': ssl_cert_full_path}

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


# Example usage
print("Starting the database connection process.")
connection = create_connection()

if connection:
    # Print that the database operations can proceed
    print("Ready to perform database operations.")
else:
    # Print if the connection was not successful
    print("Database connection could not be established.")
