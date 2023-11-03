import time
from queue import Queue

import tmdb
from flask import Flask, render_template, request, redirect, url_for, flash, jsonify
from flask_login import LoginManager, current_user, login_required, login_user, logout_user, UserMixin

from db_config import db_config, user_db_config
from scripts.get_user_account import get_user_by_id, get_all_movies_in_watchlist, insert_new_user, get_user_login, \
    get_all_watched_movie_details_by_user
from scripts.log_movie_to_account import log_movie_to_account, add_movie_to_watchlist
from scripts.movie_queue import MovieQueue
from scripts.set_filters_for_nextreel_backend import ImdbRandomMovieFetcher, extract_movie_filter_criteria
from scripts.sort_and_filter import get_filtered_watched_movies, sort_movies
from scripts.tmdb_data import get_backdrop_image_for_home


class Account(UserMixin):

    def __init__(self, id, username, email):
        self.id = id
        self.username = username
        self.email = email

    @classmethod
    def register_user(cls, username, email, password, db_config):
        return insert_new_user(username, email, password)

    @classmethod
    def login_user(cls, username, password, db_config):
        return get_user_login(username, password, db_config)

    def get_watched_movies_by_user(self, user_id):
        return get_all_watched_movie_details_by_user(user_id)

    def get_movies_in_watchlist(self, user_id):
        return get_all_movies_in_watchlist(user_id)

    def add_movie_to_watchlist(self, user_id, username, tconst, movie_data, db_config):
        return add_movie_to_watchlist(user_id, username, tconst, movie_data, db_config)

    def log_movie_to_user_account(self, user_id, username, tconst, movie_data, db_config):
        return log_movie_to_account(user_id, username, tconst, movie_data, db_config)
