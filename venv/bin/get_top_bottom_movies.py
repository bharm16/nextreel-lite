#!/Users/bryceharmon/Desktop/nextreel-lite/venv/bin/python3.9
# -*- coding: utf-8 -*-
"""
get_top_bottom_movies.py

Usage: get_top_bottom_movies

Return top and bottom 10 movies, by ratings.
"""

import sys

# Import the Cinemagoer package.
try:
    import imdb
except ImportError:
    print('You need to install the Cinemagoer package!')
    sys.exit(1)


if len(sys.argv) != 1:
    print('No arguments are required.')
    sys.exit(2)

i = imdb.IMDb()

top250 = i.get_top250_movies()
bottom100 = i.get_bottom100_movies()

for label, ml in [('top 10', top250[:10]), ('bottom 10', bottom100[:10])]:
    print('')
    print('%s movies' % label)
    print('rating\tvotes\ttitle')
    for movie in ml:
        outl = '%s\t%s\t%s' % (movie.get('rating'), movie.get('votes'),
                                movie['long imdb title'])
        print(outl)
