## Introduction
Nextreel is designed to provide a comprehensive solution for managing and interacting with movie data. It integrates with The Movie Database (TMDB) API for data retrieval and offers features like setting filters, managing a movie queue, and handling movie data operations.

## Features
Data Retrieval: Integration with TMDB API for fetching up-to-date movie data.

Filters: Customizable filters for sorting and searching through movie collections.
Movie Management: Add, update, and delete movie entries from the database.

Queue System: Manage a queue of movies for scheduled viewing or watchlist purposes.

Dynamic Query Building: Construct MySQL queries dynamically for database interactions.
Installation

Install the required Python packages: pip install -r requirements.txt.
## Dependencies
Quart is used as the async web framework. It pulls in Flask and Jinja2 automatically, so they do not need to be pinned separately.

## Configuration
Sensitive configuration such as database credentials and API keys is loaded from
environment variables. Populate the variables described in `settings.py` or use a
secrets manager (e.g., HashiCorp Vault, AWS Secrets Manager) to supply them at
runtime. Rotating these values in the environment or vault takes effect without
code changes.

The runtime.txt file should reflect the Python version compatible with your environment.

## Usage
Run the application using python app.py.
Utilize the scripts such as movie_service.py, tmdb_client.py, and others as needed.

## Contribution
Contributions to this project are welcome. Please follow the standard fork-and-pull request workflow for contributions.

## Additional Information
For any issues or feature requests, please open an issue in the repository.
Detailed documentation for each script can be found within the code comments.