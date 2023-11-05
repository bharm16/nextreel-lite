import os

from imdb import Cinemagoer

# Use os.path.dirname to go up one level from the current script's directory
parent_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Now change the working directory to the parent directory
os.chdir(parent_dir)


# create an instance of the Cinemagoer class
ia = Cinemagoer()

# get a movie
movie = ia.get_movie('0133093')
print(movie)

plot = movie.summary()
print(plot)

poster = movie.get_fullsizeURL()
print(poster)


# print the names of the directors of the movie
print('Directors:')
for director in movie['directors']:
    print(director['name'])

# # print the genres of the movie
# print('Genres:')
# for genre in movie['genres']:
#     print(genre)
#
# moviePlot = ia.get_movie('0133093', info=['plot'])
#
# # search for a person name
# people = ia.search_person('Mel Gibson')
# for person in people:
#     print(person.personID, person['name'])
