Name: Shane Battles (sbattl15)
Module Info: Module 1 Assignment: Personal Website

run.py: This is the main program for the website. It uses Flask to create the website framework. The core page is "home", while two other pages, "contact" and "projects", can be accessed through the navigation bar in the top right. The website is currently ran through a local IP host, 0.0.0.0 port 8080.

__init__.py: This program is used to initialize the pages of the website. It pulls the blueprint data from pages.py to route to the specified webpage.

pages.py: This program uses blueprints to create templates from .html files.

home.css: The default .css template used to format the website. It was created using ChatGPT, though small edits were done manually as needed.

Known bugs: None