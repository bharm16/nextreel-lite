
import mysql.connector
import pymysql

from config import Config



# db_config.py
db_config = {
    'host': 'localhost',
    'user': 'root',
    'password': 'caching_sha2_password',
    'database': 'imdb'
}

user_db_config = {
    'host': 'localhost',
    'user': 'root',
    'password': 'caching_sha2_password',
    'database': 'UserAccounts'
}



 # Ensure this is the correct import based on your project structure

# Create a connection using the STACKHERO_DB_CONFIG



def create_connection():
    try:
        return pymysql.connect(
            host=Config.STACKHERO_DB_CONFIG['host'],
            user=Config.STACKHERO_DB_CONFIG['user'],
            password=Config.STACKHERO_DB_CONFIG['password'],
            database=Config.STACKHERO_DB_CONFIG['database'],
            port=Config.STACKHERO_DB_CONFIG['port'],
            cursorclass=pymysql.cursors.DictCursor
        )
    except pymysql.MySQLError as err:
        print(f"Error: '{err}'")
        return None
# Example usage
connection = create_connection()
if connection:
    # Perform database operations
    pass
