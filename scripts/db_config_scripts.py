import os

import pymysql

import config


# print(f"Current working directory before change: {os.getcwd()}")

# Use os.path.dirname to go up one level from the current script's directory
parent_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Now change the working directory to the parent directory
os.chdir(parent_dir)

# Finally, print the new working directory to confirm the change
print(f"Current working directory after change: {os.getcwd()}")





# def show_create_statements_for_all_tables(db_config):
#     try:
#         # Connecting to the database
#         conn = config.create_connection()
#         print(f"Connected to database: {db_config['database']}")
#
#         cursor = conn.cursor()
#
#         # Showing tables only in imdb database
#         cursor.execute("SHOW TABLES IN imdb")
#         tables = cursor.fetchall()
#
#         print(f"Tables found: {tables}")
#
#         for table in tables:
#             table_name = table[0]
#             print(f"Current table name: {table_name}")  # Debug print
#
#             query = f"SHOW CREATE TABLE `imdb`.`{table_name}`"  # Enclose table name in backticks
#             print(f"Executing query: {query}")  # Debug print
#
#             cursor.execute(query)
#             create_table_statement = cursor.fetchone()[1]
#
#             print(f"Create Table Statement for {table_name}:\n{create_table_statement}\n")
#
#     except Exception as e:
#         print(f"An error occurred: {e}")
#
#     finally:
#         cursor.close()
#         conn.close()


dbconfig = config.Config.STACKHERO_DB_CONFIG
# Show CREATE TABLE statements
# show_create_statements_for_all_tables(dbconfig)

# def show_create_statements_for_all_tables_in_user_accounts(db_config):
#     try:
#         # Connecting to the database
#         conn = pymysql.connect(**db_config)
#         # print(f"Connected to database: {db_config['database']}")
#
#         cursor = conn.cursor()
#
#         # Show tables only in the UserAccounts database
#         cursor.execute("SHOW TABLES IN UserAccounts")
#         tables = cursor.fetchall()
#
#         # print(f"Tables found: {tables}")
#
#         for table in tables:
#             table_name = table[0]
#             # print(f"Current table name: {table_name}")  # Debug print
#
#             query = f"SHOW CREATE TABLE `UserAccounts`.`{table_name}`"  # Enclose table name in backticks
#             # print(f"Executing query: {query}")  # Debug print
#
#             cursor.execute(query)
#             create_table_statement = cursor.fetchone()[1]
#
#             # print(f"Create Table Statement for {table_name}:\n{create_table_statement}\n")
#             print(f" {table_name}:\n{create_table_statement}\n")
#
#     except Exception as e:
#         print(f"An error occurred: {e}")
#
#     finally:
#         cursor.close()
#         conn.close()

# Example usage

# Fetch and display the CREATE TABLE statements
# show_create_statements_for_all_tables_in_user_accounts(user_db_config)
