Name: Shane Battles (sbattl15)
Module Info: Module 2 Assignment: Web Scraping

scrape.py: This is the main program for the website. It uses BeautifulSoup and urllib to scrape thegradcafe.com. First it checks robots.txt to ensure there is permission to fetch data from Grad Cafe. Once permission is confirmed the program begins scraping one page at a time until it hits the desired number of entries based on the variable MAX_ENTRIES. Using a very long list of regex expressions, the desired data fields are pulled for each survey and added to a JSON file called "applicant_data". Some minor cleaning and organizing occurs when creating the JSON, but most of that is left to clean.py.

clean.py: This program takes "applicant_data.json" and performs baseline data cleaning and formatting to organize each survey's data into the desired format for the assignment. This is done through an extensive list of parsers that identify values and reorganize them as desired. The program then creates a JSON file called "cleaned_applicant_data" which will be used in the LLM cleaning steps inside app.py.

app.py: This program (which was provided to us) uses an LLM to further clean and organize the data from our web scraping. I made a few minor edits to the program to fix formatting issues plus added multithreading. Using lists of data provided in the "llm_hosting" folder, it creates two new fields that are useful for future analyses.

Known bugs:
scrape.py: Sometimes a CAPTCHA error will pop up and end the scraping process. The program can be reran from that last page and it will continue running. My third attempt ran the full number of entries without hitting a CAPTCHA blocker so it can be a little random.
