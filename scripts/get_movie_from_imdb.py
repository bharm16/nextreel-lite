import os

#
# print("Python Executable:", sys.executable)

# Use os.path.dirname to go up one level from the current script's directory
parent_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Now change the working directory to the parent directory
os.chdir(parent_dir)

# Finally, print the new working directory to confirm the change
# print(f"Current working directory after change: {os.getcwd()}")

#
# connection = create_connection()
#
#
# def get_random_row_value(table_name):
#     """Fetch a random row's value from a specific table and column."""
#     # SQL query to get a random row directly
#     row_query = f"SELECT * FROM `{table_name}` ORDER BY RAND() LIMIT 1"
#     # Execute the query and get the row
#     random_row = execute_query(row_query)
#     return random_row


# def main(criteria):
#     """Main function to execute the program."""
#     # Create an instance of ImdbRandomMovieFetcher
#     movie_fetcher = ImdbRandomMovieFetcher(db_config)
#
#     # Fetch a random movie row that matches the criteria using the fetch_random_movie method
#     row = movie_fetcher.fetch_random_movie(criteria)
#     if not row:
#         print("No movies found based on the given criteria.")
#         return None
#
#     # Fetch movie info from IMDb
#     movie_info = fetch_movie_info_from_imdb(row['tconst'])
#
#     print("Fetched movie genres:", movie_info.get('genres'))
#
#     return movie_info


# # Example usage
# if __name__ == "__main__":
#     criteria = {
#         "min_year": 1900,
#         "max_year": 2023,
#         "min_rating": 7.0,
#         "max_rating": 10,
#         "title_type": "movie",
#         "language": "en",
#         "genres": ["Action", "Drama"]
#     }
#     # Run the main function
#     # main(criteria)
