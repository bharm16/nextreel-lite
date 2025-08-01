    $(document).ready(function ($) {
        // Log the current value of the data-percent attribute before initializing circle progress
        console.log($('#rating-circle').data('percent')); // Should log the correct rating value * 10

        $('#rating-circle').circleProgress({
            value: $('#rating-circle').data('percent') / 10, // Adjust if the rating system is out of 10
            size: 100, // Size of the circle
            thickness: 10, // Thickness of the circle line
            fill: {
                color: '#2bb030' // Fill color
            }
        }).on('circle-animation-progress', function (event, progress, stepValue) {
            $(this).find('strong').text((stepValue * 10).toFixed(1));
        });
    });

    function formatVotes() {
        const voteElement = document.getElementById("vote-number");
        let voteCount = parseInt(voteElement.textContent, 10);
        let formattedVotes = '';
        if (voteCount >= 1000000) {
            formattedVotes = (voteCount / 1000000).toFixed(1) + 'M';
        } else if (voteCount >= 1000) {
            formattedVotes = (voteCount / 1000).toFixed(1) + 'K';
        } else {
            formattedVotes = voteCount;
        }
        voteElement.textContent = formattedVotes;
    }


    // Updated JavaScript code
    document.addEventListener("DOMContentLoaded", function () {
        // Locate the genre-info element by its id
        const genreInfoElement = document.getElementById("genre-info");

        // Read the full genres text
        const fullGenres = genreInfoElement.textContent || genreInfoElement.innerText;

        // Clear out the existing content (if any)
        genreInfoElement.innerHTML = '';

        // Split the genres by comma (or whatever delimiter is used)
        const genresArray = fullGenres.split(',');

        // Limit the number of genres to display
        const maxGenresToShow = 3;

        // Loop through each genre and create a button, but only up to maxGenresToShow
        for (let i = 0; i < Math.min(genresArray.length, maxGenresToShow); i++) {
            // Create a new button element
            const btn = document.createElement("button");

            // Add text to the button
            btn.innerHTML = genresArray[i].trim();  // Using trim() to remove any leading/trailing whitespace

            // Add the custom CSS class to the button
            btn.className = "genre-custom-button";

            // Append the button to the genre-info div
            genreInfoElement.appendChild(btn);
        }
    });


    formatVotes();


    document.addEventListener("DOMContentLoaded", function () {
        const seenItButton = document.getElementById("seen-it-button");
        seenItButton.addEventListener("click", function () {
            const tconst = this.getAttribute("data-tconst");
            fetch("/seen_it", {
                method: "POST",
                headers: {
                    "Content-Type": "application/json"
                },
                body: JSON.stringify({
                    tconst: tconst
                }),
                redirect: 'follow'  // Automatically follow redirects
            }).then(response => {
                if (response.ok) {
                    window.location.href = response.url;  // Navigate to the new URL
                } else {
                    alert("Failed to mark movie as seen!");
                }
            }).catch(error => console.error("Error:", error));


        });

    });

    // Capture the add-to-watchlist button and add a click event listener


    document.addEventListener("DOMContentLoaded", function () {
        const addToWatchlistButton = document.getElementById("add-to-watchlist-button");
        addToWatchlistButton.addEventListener("click", function () {
            const tconst = this.getAttribute("data-tconst");
            fetch("/add_to_watchlist", {
                method: "POST",
                headers: {
                    "Content-Type": "application/json"
                },
                body: JSON.stringify({
                    tconst: tconst
                }),
                redirect: 'follow'  // Automatically follow redirects
            }).then(response => {
                if (response.ok) {
                    window.location.href = response.url;  // Navigate to the new URL
                } else {
                    alert("Failed to add movie to watchlist!");
                }
            }).catch(error => console.error("Error:", error));
        });
    });


    document.addEventListener("DOMContentLoaded", function () {
        const playButton = document.getElementById("play-button");
        playButton.addEventListener("click", function () {
            const videoUrl = this.getAttribute("data-video-url"); // Read the video URL from data attribute
            if (videoUrl) {
                window.open(videoUrl, '_blank'); // Open the video URL in a new tab
            } else {
                alert("Video URL not available");
            }
        });
    });

    document.addEventListener("DOMContentLoaded", function () {
        // Capture the left-arrow button and add a click event listener
        const leftArrowButton = document.getElementById("left-arrow-button");
        leftArrowButton.addEventListener("click", function () {
            // Do something for the previous movie
            // You can programmatically submit the /previous_movie form here
        });

        // Capture the right-arrow button and add a click event listener
        const rightArrowButton = document.getElementById("right-arrow-button");
        rightArrowButton.addEventListener("click", function () {
            // Do something for the next movie
            // You can programmatically submit the /next_movie form here
        });
    });


