# evryquiktool

This is the source code for evryquiktool. A project that aims to enhance my web development skills using HTML5, CSS3, JavaScript, Python, and Flask. It is a static website currently, however in future developments of the site, a backend will be integrated using Django, authentication will be implemented using either OAuth or Google Sign-In, secure payment transactions using Flask, and of course more tools for users to access easily and quickly.

## Overview

This website is designed to be simple, responsive, and user friendly. The function of the site is to help users achieve their most common needs in less than 3 clicks. The quicker the user gets what they want done, the happier we are to cut down their time through searching the web or looking through legitimate/sketchy websites. The site features a landing page, and from there, responsive cards for the user to easily access the tool they are looking to use. The website is optimized for performance and accessibility, ensuring a seamless experience across all devices.

## Features

- **Responsive Design**: The website is fully responsive, adapting to different screen sizes from mobile phones to large desktop monitors.
- **Landing Page**: A page with the site's name, a banner section, a card section with all the tools the site has to offer, and a contact form, encouraging users to provide feedback of a tool they would like to see developed in the future or if there are any issues with any of the currently existing tools. 
- **Tool Pages**: Pages dedicated to each tool developed. The user is able to achieve what they are seeking to accomplish in less than 3 clicks. 
- **Upload & Download Pages**: Upload and download pages contaning all the user's uploads to and downloads from the site depending on which tool they chose to use.

## Technologies Used

- **HTML5**: For the basic structure of the website.
- **CSS3**: For styling the website and ensuring it is responsive and visually appealing.
- **JavaScript**: For interactivity and smooth scrolling.
- **Python**: For functional integration for each of the tools and smooth page access. 

## Project Structure

```plaintext
├── downloads
│   ├── combine
│   └── youtube
├── static
│   └── custom.js
├── templates
│   ├── activity_combiner.html
│   ├── base.html
│   ├── combine.html
│   ├── downloads.html
│   ├── landing.html
│   ├── map_viewer.html
│   ├── math_proofs.png
│   ├── uploads.html
│   └── youtube.html
├── uploads
│   ├── combine
│   └── youtube
├── app.py
├── README.md
└── requirements.txt
```

## Future Enhancements
- Connect form submissions to a backend and email to recieve direct messages via the company email and keep track of messages sent via a spreadsheet.
- Implement currently disabled tools.
- Add user authentication for personalized experience.
- Transition from a free tier website to a subscription based website with some free tier capabilities, whilst others requiring a monthly or yearly subscription.
