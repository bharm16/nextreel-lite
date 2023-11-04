import time
from queue import Queue

import tmdb
from flask import Flask, render_template, request, redirect, url_for, flash, jsonify
from flask_login import LoginManager, current_user, login_required, login_user, logout_user, UserMixin


from scripts.movie_queue import MovieQueue
from scripts.set_filters_for_nextreel_backend import ImdbRandomMovieFetcher, extract_movie_filter_criteria
from scripts.sort_and_filter import get_filtered_watched_movies, sort_movies
from scripts.tmdb_data import get_backdrop_image_for_home

